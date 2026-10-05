"""PulseAudio-compatible Linux capture; never changes the default input/output."""

import json
import subprocess
import threading
import uuid
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AudioDevice:
    name: str
    description: str
    monitor: str = ""


def parse_devices(sinks, sources):
    outputs = [
        AudioDevice(x["name"], x.get("description", x["name"]), x["monitor_source"])
        for x in sinks
        if x.get("monitor_source")
    ]
    microphones = [
        AudioDevice(x["name"], x.get("description", x["name"]))
        for x in sources
        if x.get("monitor_of_sink", 4294967295) == 4294967295 and not x["name"].endswith(".monitor")
    ]
    return outputs, microphones


def pactl(*args):
    try:
        result = subprocess.run(["pactl", *args], capture_output=True, timeout=5, check=True)
        return result.stdout.decode().strip()
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("Audio server unavailable or rejected the operation.") from None


def list_devices():
    return parse_devices(
        json.loads(pactl("-f", "json", "list", "sinks")), json.loads(pactl("-f", "json", "list", "sources"))
    )


def recording_command(source: str):
    if not source or "\x00" in source:
        raise ValueError("Select a valid audio source.")
    return [
        "parec",
        f"--device={source}",
        "--format=s16le",
        "--rate=16000",
        "--channels=1",
        "--latency-msec=100",
        "--property=application.name=MeetingCopilotCapture",
    ]


class AudioCapture(Protocol):
    def start(self, source, on_pcm, on_status): ...
    def stop(self): ...


class PulseCapture:
    def __init__(self):
        self.stopped = threading.Event()
        self.process = None
        self.thread = None
        self.lock = threading.Lock()

    def start(self, source, on_pcm, on_status):
        if self.thread and self.thread.is_alive():
            raise RuntimeError("Capture already running.")
        self.stopped.clear()

        def capture():
            delay = 1
            while not self.stopped.is_set():
                try:
                    available = json.loads(pactl("-f", "json", "list", "sources"))
                    if source not in {d["name"] for d in available}:
                        raise RuntimeError("Selected device is disconnected.")
                    with self.lock:
                        if self.stopped.is_set():
                            break
                        self.process = subprocess.Popen(
                            recording_command(source), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
                        )
                        process = self.process
                    on_status("Capturing selected audio")
                    while not self.stopped.is_set():
                        pcm = process.stdout.read(8000)
                        if not pcm:
                            raise RuntimeError("Capture stream ended.")
                        on_pcm(pcm)
                        delay = 1
                except (OSError, RuntimeError, ValueError):
                    if not self.stopped.is_set():
                        on_status("Audio unavailable; reconnecting selected device (gap in capture)")
                finally:
                    self._terminate()
                if self.stopped.wait(delay):
                    break
                delay = min(delay * 2, 8)
            on_status("Capture stopped")

        self.thread = threading.Thread(target=capture, daemon=True)
        self.thread.start()

    def _terminate(self):
        with self.lock:
            process, self.process = self.process, None
        if process:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            process.stdout.close()

    def stop(self):
        self.stopped.set()
        self._terminate()
        if self.thread:
            self.thread.join(timeout=3)


def owned_module_ids(text, name):
    found = set()
    for row in text.splitlines():
        parts = row.split("\t")
        if len(parts) >= 3 and parts[0].isdigit():
            tokens = parts[2].split()
            if f"sink_name={name}" in tokens or f"source={name}.monitor" in tokens:
                found.add(parts[0])
    return found


class MeetingRoute:
    """Owns a unique virtual sink and optional loopback, never Meet's microphone."""

    def __init__(self, description="MeetingCopilot"):
        self.name = "meeting_copilot_" + uuid.uuid4().hex
        self.description = description
        self.modules = []
        self.monitor = ""

    def create(self, output_name=None):
        if self.modules:
            raise RuntimeError("Route already created.")
        if output_name and output_name not in {d.name for d in list_devices()[0]}:
            raise ValueError("Selected playback output is unavailable.")
        try:
            module = pactl(
                "load-module",
                "module-null-sink",
                f"sink_name={self.name}",
                f"sink_properties=device.description={self.description}",
            )
            self.modules.append(module)
            self.monitor = next(d.monitor for d in list_devices()[0] if d.name == self.name)
            if output_name:
                module = pactl(
                    "load-module",
                    "module-loopback",
                    f"source={self.monitor}",
                    f"sink={output_name}",
                    "source_dont_move=true",
                    "sink_dont_move=true",
                    "latency_msec=100",
                )
                self.modules.append(module)
            return self.name, self.monitor
        except Exception:
            self.close()
            raise

    def close(self):
        # IDs can be reused after an audio-server restart; also verify the unique route name.
        try:
            active = owned_module_ids(pactl("list", "short", "modules"), self.name)
            for module in reversed(self.modules):
                if module in active:
                    pactl("unload-module", module)
        finally:
            self.modules.clear()
