"""Explicit integration check: only records a synthetic tone from a private null sink."""

import array
import math
import subprocess
import tempfile
import threading
import wave
from pathlib import Path

from meeting_copilot.audio import MeetingRoute, PulseCapture


def main():
    route = MeetingRoute()
    synthetic_output = MeetingRoute(description="SyntheticHeadphones")
    capture = PulseCapture()
    detected = threading.Event()

    def inspect(pcm):
        if any(abs(x) > 100 for x in array.array("h", pcm)):
            detected.set()

    try:
        output, monitor = synthetic_output.create()  # Destination is inaudible, not real headphones.
        name, _ = route.create(output)
        capture.start(monitor, inspect, lambda _: None)
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "tone.wav"
            with wave.open(str(audio), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(16000)
                out.writeframes(
                    array.array(
                        "h", (int(10000 * math.sin(i * 2 * math.pi * 440 / 16000)) for i in range(32000))
                    ).tobytes()
                )
            subprocess.run(["paplay", f"--device={name}", str(audio)], check=True, timeout=10)
            assert detected.wait(3), "Monitor did not receive the synthetic tone."
        print("Synthetic fixed-loopback and monitor capture passed; no microphone or physical output used.")
    finally:
        capture.stop()
        route.close()
        synthetic_output.close()


if __name__ == "__main__":
    main()
