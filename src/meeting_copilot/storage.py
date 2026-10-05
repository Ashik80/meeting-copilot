"""SQLite feedback and opt-in transcript retention. Default is memory-only."""

import os
import sqlite3
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol


def write_private(path, text):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".copilot-export-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class InsightStore(Protocol):
    def feedback(self, insight_id: int, action: str): ...


class LocalStore:
    def __init__(self, path=None, keep_transcript=False):
        self.path = Path(path) if path else None
        self.keep_transcript = keep_transcript
        self.lock = threading.Lock()
        if self.path:
            # Never overwrite or adopt an existing database containing someone else's meeting.
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        self.db = sqlite3.connect(str(self.path) if self.path else ":memory:", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=MEMORY")
        self.db.execute(
            "CREATE TABLE transcript(id TEXT PRIMARY KEY, timestamp REAL, source TEXT, text TEXT)"
        )
        self.db.execute(
            "CREATE TABLE feedback(insight_id INTEGER, action TEXT, timestamp TEXT DEFAULT CURRENT_TIMESTAMP)"
        )

    def transcript(self, entry):
        if self.keep_transcript:
            with self.lock, self.db:
                self.db.execute(
                    "INSERT INTO transcript VALUES(?,?,?,?)",
                    (entry["id"], entry["timestamp"], entry["source"], entry["text"]),
                )

    def feedback(self, insight_id, action):
        if action not in ("useful", "not_useful", "saved", "dismissed"):
            raise ValueError("Unsupported feedback action.")
        with self.lock, self.db:
            self.db.execute("INSERT INTO feedback(insight_id,action) VALUES(?,?)", (insight_id, action))

    def export_transcript(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT timestamp,source,text FROM transcript ORDER BY timestamp,id"
            ).fetchall()
        return "\n".join(
            f"[{datetime.fromtimestamp(t, UTC).astimezone().strftime('%Y-%m-%d %H:%M:%S %z')}] {source}: {text}"
            for t, source, text in rows
        )

    def close(self):
        with self.lock:
            self.db.close()

    def delete(self):
        self.close()
        if self.path:
            self.path.unlink(missing_ok=True)
