"""Independent capture, STT and analysis workers; no audio written to disk."""

import array
import json
import math
import queue
import threading
import time

from meeting_copilot.audio import PulseCapture
from meeting_copilot.benchmark import LocalWhisper
from meeting_copilot.intelligence import MeetingContext, QuietAnalyzer
from meeting_copilot.llm import AnalysisError
from meeting_copilot.project import TextProjectContext
from meeting_copilot.storage import LocalStore


class ChunkBuffer:
    def __init__(self, seconds=8):
        self.size = int(seconds * 32000)
        self.buffers = {}

    def feed(self, source, data, timestamp):
        buffer = self.buffers.setdefault(source, bytearray())
        buffer.extend(data)
        packets = []
        while len(buffer) >= self.size:
            packets.append((source, bytes(buffer[: self.size]), timestamp))
            del buffer[: self.size]
        return packets

    def flush(self):
        packets = [
            (source, bytes(data), time.time()) for source, data in self.buffers.items() if len(data) >= 6400
        ]
        self.buffers.clear()
        return packets


class Session:
    def __init__(
        self,
        on_event,
        model="small",
        llm=None,
        mode="Technical",
        project="",
        store=None,
        threshold=0.85,
        tts=None,
        stt_factory=None,
    ):
        self.emit = on_event
        self.model_name = model
        self.stt_factory = stt_factory or (lambda: LocalWhisper(model))
        self.llm, self.mode = llm, mode
        self.project = TextProjectContext(project) if isinstance(project, str) else project
        self.store = store or LocalStore()
        self.context = MeetingContext()
        self.analyzer = QuietAnalyzer(self.context, threshold=threshold)
        self.tts = tts
        self.lock = threading.RLock()
        self.chunks = ChunkBuffer()
        self.audio = queue.Queue(maxsize=16)
        self.captures = []
        self.stt_done = threading.Event()
        self.finish_analysis = threading.Event()
        self.finished = threading.Event()
        self.active = False
        self.stopping = False
        self.sources = []
        self.usage = {"audio_seconds": 0.0, "transcribed_seconds": 0.0, "stt_failures": 0, "audio_gaps": 0}
        self.started_at = None
        self.last_analysis_error = ""
        self.analysis_failures = 0
        self.rejected_insights = 0

    def start(self, system_source, microphone_source=None):
        if self.started_at is not None or not system_source:
            raise ValueError("Select a system monitor and use a fresh meeting session.")
        self.started_at = time.time()
        self.sources = [("Meeting", system_source)] + (
            [("Me", microphone_source)] if microphone_source else []
        )
        self.stt_thread = threading.Thread(target=self._stt, daemon=True)
        self.analysis_thread = threading.Thread(target=self._analysis, daemon=True)
        self.stt_thread.start()
        self.analysis_thread.start()
        self.resume()

    def _packet(self, packet):
        try:
            self.audio.put_nowait(packet)
        except queue.Full:
            with self.lock:
                self.usage["audio_gaps"] += 1
            self.emit("status", "STT backlog full: audio chunk lost. Try Base or disable microphone capture.")

    def _pcm(self, source, data):
        with self.lock:
            if not self.active:
                return
            self.usage["audio_seconds"] += len(data) / 32000
            packets = self.chunks.feed(source, data, time.time())
        for packet in packets:
            self._packet(packet)

    def resume(self):
        if self.active or self.stopping:
            return
        self.active = True
        self.captures = []
        for label, source in self.sources:
            capture = PulseCapture()
            capture.start(
                source,
                lambda pcm, label=label: self._pcm(label, pcm),
                lambda status, label=label: self.emit("capture", f"{label}: {status}"),
            )
            self.captures.append(capture)
        self.emit("state", "Listening — local transcription")

    def pause(self):
        if self.stopping or not self.active:
            return
        self.active = False
        for capture in self.captures:
            capture.stop()
        self.captures = []
        with self.lock:
            remaining = self.chunks.flush()
        for packet in remaining:
            self._packet(packet)
        if self.tts:
            self.tts.mute()
        self.emit("state", "Paused — no new audio captured; queued work may finish")

    def _stt(self):
        import numpy as np

        model = None
        self.emit("status", "Loading local Whisper model; audio queue is bounded")
        while not self.stt_done.is_set() or not self.audio.empty():
            try:
                source, pcm, timestamp = self.audio.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                values = array.array("h", pcm)
                if not values or math.sqrt(sum(x * x for x in values) / len(values)) < 50:
                    continue
                if model is None:
                    model = self.stt_factory()
                text = model.transcribe(np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768)
                with self.lock:
                    self.usage["transcribed_seconds"] += len(pcm) / 32000
                    entry = self.context.add(text, source, timestamp)
                    if entry:
                        try:
                            self.store.transcript(entry)
                        except Exception:  # noqa: BLE001 -- storage adapters must not stop transcription
                            self.emit("status", "Transcript storage failed; text remains in recent memory.")
                if entry:
                    self.emit("transcript", entry)
            except Exception:  # noqa: BLE001 -- provider errors may contain speech; keep capture alive
                with self.lock:
                    self.usage["stt_failures"] += 1
                model = None
                self.emit("status", "STT failed for a chunk; gap marked, retrying on next chunk.")
            finally:
                self.audio.task_done()

    def _analyze_once(self, final=False):
        with self.lock:
            if not self.llm or not self.context.pending():
                return
            now = time.time()
            if not final and not self.analyzer.ready(now):
                return
            self.analyzer.last_attempt = now
            request = self.context.request(self.mode, self.project, self.analyzer.issues)
            body = json.loads(request)
            evidence_ids = {e["id"] for e in body["recent_transcript"] + body["new_transcript"]}
            evidence_ids.update(e for fact in body["running_summary"] for e in fact["evidence_ids"])
            if body["project_context"]["text"]:
                evidence_ids.add("project:1")
            # Advance only through the chunk actually sent, not speech arriving during the HTTP request.
            through = max(int(e["id"][1:]) for e in body["new_transcript"])
            if body["context_clipped"] or body["dropped_pending_entries"]:
                self.emit(
                    "status",
                    "Context bounded: older detail omitted. Review unresolved facts and capture gaps.",
                )
        try:
            response = self.llm.analyze(request)
            with self.lock:
                try:
                    insights = self.analyzer.accept(response, through=through, allowed=evidence_ids)
                except ValueError as exc:
                    raise AnalysisError("Analysis response rejected: " + str(exc)) from None
                topic = self.context.topic
                self.last_analysis_error = ""
                rejected = self.analyzer.rejected_insights
                self.rejected_insights += rejected
            self.emit(
                "analysis_status",
                "Analysis accepted; meeting context updated."
                + (f" {rejected} invalid finding(s) omitted." if rejected else ""),
            )
            self.emit("topic", topic)
            for insight in insights:
                self.emit("insight", insight)
                if self.tts and self.active and not self.stopping:
                    try:
                        self.tts.speak(insight)
                    except Exception:  # noqa: BLE001 -- speech adapters cannot stop insight delivery
                        self.emit("status", "TTS unavailable; analysis and visible insights continue.")
        except AnalysisError as exc:
            self.analysis_failures += 1
            self.last_analysis_error = str(exc)
            self.emit("analysis_status", self.last_analysis_error)
            self.emit("status", self.last_analysis_error)
        except Exception:  # noqa: BLE001 -- custom provider exceptions may contain secrets/speech
            self.analysis_failures += 1
            self.last_analysis_error = (
                "Analysis failed; pending speech retained. Local transcription continues."
            )
            self.emit("analysis_status", self.last_analysis_error)
            self.emit("status", self.last_analysis_error)
        self.emit("usage", self.stats())

    def _analysis(self):
        while not self.finish_analysis.wait(1):
            self._analyze_once()
        for _ in range(20):
            before = self.context.watermark
            self._analyze_once(final=True)
            if self.context.watermark == before or not self.context.pending():
                break
        if self.context.pending():
            self.emit("status", "Final summary is incomplete: some speech was not analyzed.")
        with self.lock:
            explanation = self.last_analysis_error or (
                "Cloud analysis was disabled; only local transcription was enabled." if not self.llm else ""
            )
            summary = self.analyzer.summary_markdown(explanation)
            available = self.context.watermark > 0
            incomplete = bool(self.context.pending() or self.context.dropped_pending)
        self.emit("summary", summary)
        self.finished.set()
        self.emit(
            "state",
            "Stopped — summary unavailable"
            if not available
            else "Stopped — partial summary"
            if incomplete
            else "Stopped — summary ready",
        )

    def stop(self):
        if self.stopping:
            return
        self.stopping = True
        self.active = False
        self.emit("state", "Stopping — draining transcription and final analysis")

        def finish():
            for capture in self.captures:
                capture.stop()
            if self.tts:
                self.tts.mute()
            with self.lock:
                remaining = self.chunks.flush()
            for packet in remaining:
                self._packet(packet)
            self.stt_done.set()
            self.stt_thread.join()
            self.finish_analysis.set()

        threading.Thread(target=finish, daemon=True).start()

    def feedback(self, insight_id, action):
        with self.lock:
            for item in self.analyzer.issues:
                if item["id"] == insight_id and action in ("dismissed", "saved", "not_useful"):
                    item["status"] = action
            self.store.feedback(insight_id, action)
        if action == "dismissed" and self.tts:
            self.tts.mute()

    def stats(self):
        with self.lock:
            return {
                **self.usage,
                "duration_seconds": time.time() - self.started_at if self.started_at else 0,
                "analysis_failures": self.analysis_failures,
                "rejected_insights": self.rejected_insights,
                "analyzed_through": self.context.watermark,
                "pending_analysis_entries": len(self.context.pending()),
                "dropped_pending_entries": self.context.dropped_pending,
                **(getattr(self.llm, "usage", {}) if self.llm else {}),
            }
