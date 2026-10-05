"""Optional local Piper speech, isolated from meeting capture and microphone inputs."""

import queue
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Protocol

from meeting_copilot.audio import MeetingRoute, list_devices


def should_speak(insight, mode):
    return (
        mode == "all"
        or mode == "high"
        and insight["priority"] == "HIGH"
        or mode == "questions"
        and insight["type"] == "QUESTION"
        or mode == "questions_concerns"
        and insight["type"] in ("QUESTION", "CONCERN")
    )


class TTSProvider(Protocol):
    def speak(self, insight): ...
    def mute(self): ...
    def close(self): ...


class PiperTTS:
    def __init__(self, model_path, config_path, sink, mode="high", on_status=lambda _: None):
        if not shutil.which("piper"):
            raise ValueError("Install Piper and a local English voice before enabling TTS.")
        if not Path(model_path).is_file() or not Path(config_path).is_file():
            raise ValueError("Piper model/config files are missing.")
        self.model_path, self.config_path, self.sink = model_path, config_path, sink
        self.mode, self.status = mode, on_status
        self.items = queue.Queue(maxsize=2)
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.muted = False
        self.generation = 0
        self.process = None
        # A fixed loopback, not default-sink playback, prevents rescue to laptop speakers.
        self.route = MeetingRoute(description="MeetingCopilotTTS")
        self.playback_sink, _ = self.route.create(sink)
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def speak(self, insight):
        with self.lock:
            if self.muted or self.closed.is_set() or not should_speak(insight, self.mode):
                return
            item = (self.generation, time.time(), insight["text"][:600])
        try:
            self.items.put_nowait(item)
        except queue.Full:
            self.status("TTS queue full; stale speech skipped.")

    def mute(self):
        with self.lock:
            self.muted = True
            self.generation += 1
            if self.process and self.process.poll() is None:
                self.process.terminate()
        while True:
            try:
                self.items.get_nowait()
            except queue.Empty:
                break

    def unmute(self):
        with self.lock:
            self.muted = False

    def _valid(self, generation):
        with self.lock:
            return not self.muted and not self.closed.is_set() and self.generation == generation

    def _run(self, command, generation, text=None):
        with self.lock:
            if self.muted or self.closed.is_set() or self.generation != generation:
                return False
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE if text else subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            process = self.process
        try:
            process.communicate(input=text.encode() if text else None, timeout=45)
            return process.returncode == 0 and self._valid(generation)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            with self.lock:
                if self.process is process:
                    self.process = None

    def _worker(self):
        while not self.closed.is_set():
            try:
                generation, created, text = self.items.get(timeout=0.5)
            except queue.Empty:
                continue
            if time.time() - created > 30 or not self._valid(generation):
                continue
            try:
                if self.sink not in {d.name for d in list_devices()[0]}:
                    self.mute()
                    self.status("Headphones unavailable: TTS muted, no speaker fallback.")
                    continue
                with tempfile.TemporaryDirectory(prefix="copilot-tts-") as directory:
                    output = Path(directory) / "speech.wav"
                    if not self._run(
                        [
                            "piper",
                            "--model",
                            self.model_path,
                            "--config",
                            self.config_path,
                            "--output_file",
                            str(output),
                        ],
                        generation,
                        text,
                    ):
                        continue
                    self._run(
                        [
                            "paplay",
                            f"--device={self.playback_sink}",
                            "--property=application.name=MeetingCopilotTTS",
                            str(output),
                        ],
                        generation,
                    )
            except Exception:  # noqa: BLE001 -- TTS failures never stop analysis or expose speech
                self.status("TTS unavailable; insight remains visible.")

    def close(self):
        self.closed.set()
        self.mute()
        self.thread.join(timeout=3)
        self.route.close()
