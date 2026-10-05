import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import stat
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from meeting_copilot.audio import AudioDevice
from meeting_copilot.panel import MeetingCopilotPanel

app = QApplication.instance() or QApplication([])


class PanelTests(unittest.TestCase):
    def make_panel(self):
        devices = (
            [
                AudioDevice("speakers", "Speakers", "speakers.monitor"),
                AudioDevice("meeting_copilot_old", "Virtual", "virtual.monitor"),
            ],
            [AudioDevice("mic", "Microphone")],
        )
        with patch("meeting_copilot.panel.audio.list_devices", return_value=devices):
            panel = MeetingCopilotPanel()
        self.addCleanup(panel.close)
        return panel

    def test_analysis_error_remains_visible_when_stt_status_changes(self):
        panel = self.make_panel()
        panel._on_event("analysis_status", "Analysis HTTP 429")
        panel._on_event("status", "Local STT ready")
        self.assertEqual(panel.analysis_label.text(), "Analysis HTTP 429")
        panel._clear_now()
        self.assertNotIn("429", panel.analysis_label.text())

    def test_launch_is_idle_and_devices_are_safe_defaults(self):
        panel = self.make_panel()
        self.assertIsNone(panel.session)
        self.assertIsNone(panel.route)
        self.assertEqual(panel.output_combo.currentData(), "speakers")
        self.assertNotIn(
            "meeting_copilot_old", [panel.output_combo.itemData(i) for i in range(panel.output_combo.count())]
        )
        self.assertFalse(panel.mic_check.isChecked())
        self.assertFalse(panel.mic_combo.isEnabled())
        self.assertFalse(panel.retain_check.isChecked())
        self.assertEqual(panel.stt_combo.currentText(), "small")
        self.assertFalse(panel.cloud_check.isChecked())
        self.assertTrue(panel.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_cloud_controls_require_consent_and_key_is_password_mode(self):
        with patch.dict(
            os.environ, {"OPENROUTER_API_KEY": "environment-key", "OPENROUTER_MODEL": "example/model"}
        ):
            panel = self.make_panel()
        self.assertEqual(panel.key_edit.text(), "environment-key")
        self.assertEqual(panel.model_edit.text(), "example/model")
        self.assertEqual(panel.key_edit.echoMode(), panel.key_edit.EchoMode.Password)
        self.assertFalse(panel.cloud_settings.isEnabled())
        panel.cloud_check.setChecked(True)
        self.assertTrue(panel.cloud_settings.isEnabled())
        self.assertIn("provider", panel.privacy_label.text().lower())

    def test_retention_creates_private_app_owned_database_only_when_requested(self):
        panel = self.make_panel()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("meeting_copilot.panel.QStandardPaths.writableLocation", return_value=directory),
        ):
            memory_store = panel._new_store()
            self.assertIsNone(memory_store.path)
            memory_store.close()
            panel.retain_check.setChecked(True)
            store = panel._new_store()
            self.assertTrue(store.path.exists())
            self.assertEqual(stat.S_IMODE(store.path.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o600)
            store.delete()

    def test_event_bridge_updates_status_and_bounds_insight_cards(self):
        panel = self.make_panel()
        worker = threading.Thread(target=panel.worker_event, args=("capture", "Meeting: Capturing"))
        worker.start()
        worker.join()
        panel.worker_event("topic", "Payments")
        panel.worker_event("transcript", {"source": "Meeting", "text": "hello"})
        for number in range(10):
            panel.worker_event(
                "insight",
                {
                    "id": number,
                    "type": "RISK",
                    "priority": "HIGH",
                    "text": f"Risk {number}",
                    "reason": "Because",
                    "evidence_ids": ["t1"],
                },
            )
        app.processEvents()
        self.assertEqual(panel.capture_label.text(), "Meeting: Capturing")
        self.assertEqual(panel.topic_label.text(), "Payments")
        self.assertIn("Meeting: hello", panel.transcript.toPlainText())
        self.assertEqual(panel.insights_layout.count(), 8)
        self.assertIn("Because", panel.insights_layout.itemAt(7).widget().toolTip())

    def test_transcript_is_plain_text_and_recent_history_is_bounded(self):
        panel = self.make_panel()
        panel._on_event("transcript", {"source": "Meeting", "text": "<b>not markup</b>"})
        self.assertIn("<b>not markup</b>", panel.transcript.toPlainText())
        for i in range(400):
            panel._on_event("transcript", {"source": "Meeting", "text": str(i)})
        self.assertLessEqual(panel.transcript.document().blockCount(), 300)

    def test_project_text_is_bounded(self):
        panel = self.make_panel()
        panel.project_edit.setPlainText("x" * (panel.PROJECT_LIMIT + 1))
        self.assertFalse(panel._project_text())
        panel.project_edit.setPlainText("context")
        self.assertEqual(panel._project_text(), "context")

    def test_empty_user_data_path_never_falls_back_to_working_directory(self):
        panel = self.make_panel()
        panel.retain_check.setChecked(True)
        with (
            patch("meeting_copilot.panel.QStandardPaths.writableLocation", return_value=""),
            self.assertRaises(RuntimeError),
        ):
            panel._new_store()

    def test_created_route_locks_the_physical_output(self):
        panel = self.make_panel()
        route = Mock()
        route.create.return_value = ("private-sink", "private.monitor")
        with patch("meeting_copilot.panel.audio.MeetingRoute", return_value=route):
            panel.create_route()
        self.assertFalse(panel.output_combo.isEnabled())
        self.assertIn("pavucontrol", panel.route_label.text())

    def test_finished_meeting_must_be_cleared_before_another_start(self):
        panel = self.make_panel()
        session = Mock()
        session.finished = threading.Event()
        session.finished.set()
        panel.session = session
        panel._finished()
        self.assertFalse(panel.start_button.isEnabled())

    def test_clear_removes_sensitive_in_app_context(self):
        panel = self.make_panel()
        panel.project_edit.setPlainText("private project")
        panel.usage_label.setText("Usage: private")
        panel._clear_now()
        self.assertFalse(panel.project_edit.toPlainText())
        self.assertEqual(panel.usage_label.text(), "Usage: 0 seconds")

    def test_close_understands_worker_event_flags_and_starts_async_drain(self):
        panel = self.make_panel()
        session = Mock()
        session.finished = threading.Event()
        session.stopping = threading.Event()
        panel.session = session
        event = Mock()
        with patch("meeting_copilot.panel.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            panel.closeEvent(event)
        session.stop.assert_called_once_with()
        event.ignore.assert_called_once_with()
        session.finished.set()


if __name__ == "__main__":
    unittest.main()
