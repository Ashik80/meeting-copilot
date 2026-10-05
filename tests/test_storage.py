import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meeting_copilot.storage import LocalStore, write_private


class StorageTests(unittest.TestCase):
    def test_export_failure_does_not_destroy_previous_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.md"
            path.write_text("old summary")
            with (
                patch("meeting_copilot.storage.os.replace", side_effect=OSError("disk failure")),
                self.assertRaises(OSError),
            ):
                write_private(path, "new summary")
            self.assertEqual(path.read_text(), "old summary")
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_export_is_private_even_when_replacing_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.md"
            path.write_text("old")
            path.chmod(0o644)
            write_private(path, "private summary")
            self.assertEqual(path.read_text(), "private summary")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_default_never_stores_transcript(self):
        store = LocalStore()
        store.transcript({"id": "t1", "text": "private", "timestamp": 0, "source": "Meeting"})
        self.assertEqual(store.export_transcript(), "")
        store.close()

    def test_explicit_retention_and_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "meeting.sqlite"
            store = LocalStore(path, keep_transcript=True)
            store.transcript({"id": "t1", "text": "Test speech", "timestamp": 0, "source": "Meeting"})
            store.feedback(1, "useful")
            self.assertIn("Test speech", store.export_transcript())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            store.close()


if __name__ == "__main__":
    unittest.main()
