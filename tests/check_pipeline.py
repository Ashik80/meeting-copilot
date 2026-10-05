"""Explicit local integration check using a user-supplied WAV and an inaudible null sink."""

import argparse
import subprocess

from meeting_copilot.audio import MeetingRoute
from meeting_copilot.pipeline import Session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav")
    args = parser.parse_args()
    route = MeetingRoute()
    events = []
    session = Session(lambda kind, payload: events.append((kind, payload)), model="base")
    try:
        sink, monitor = route.create()  # No physical output, microphone, or cloud provider.
        session.start(monitor)
        subprocess.run(["paplay", f"--device={sink}", args.wav], check=True, timeout=90)
        session.stop()
        assert session.finished.wait(90), "Pipeline did not finish."
        assert session.context.recent, "No stable transcript recognized."
        assert any(kind == "summary" for kind, _ in events), "No final summary event."
        assert session.store.path is None and session.store.export_transcript() == "", (
            "Unexpected persistence."
        )
        print("Local monitor → chunked STT → recent context → stop/summary passed. No cloud or mic used.")
    finally:
        if session.started_at and not session.stopping:
            session.stop()
        if session.started_at:
            session.finished.wait(10)
        route.close()
        session.store.close()


if __name__ == "__main__":
    main()
