"""Compact Qt Widgets panel for the meeting copilot."""

from __future__ import annotations

import html
import os
import threading
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from meeting_copilot import audio
from meeting_copilot.llm import DEFAULT_INPUT_PRICE, DEFAULT_MODEL, DEFAULT_OUTPUT_PRICE, OpenRouterLLM
from meeting_copilot.pipeline import Session
from meeting_copilot.storage import LocalStore, write_private


class EventBridge(QObject):
    event = Signal(str, object)


class MeetingCopilotPanel(QMainWindow):
    PROJECT_LIMIT = 12_000

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Meeting Copilot")
        self.resize(560, 760)
        self.setMinimumSize(380, 260)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.session = self.route = self.tts = None
        self.route_output = self.route_monitor = ""
        self.summary = ""
        self._close_after_stop = self._clear_after_stop = False
        self._usage_busy = False
        self.capture_states = {}
        self.bridge = EventBridge(self)
        self.bridge.event.connect(self._on_event)
        self._build_ui()
        self._shortcuts()
        self.refresh_devices()
        self._set_running(False)
        self.usage_timer = QTimer(self)
        self.usage_timer.timeout.connect(self._usage_tick)
        self.usage_timer.start(2_000)

    def _build_ui(self):
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        collapse = QAction("Collapse", self)
        collapse.triggered.connect(self._toggle_collapse)
        toolbar.addAction(collapse)
        theme = QAction("Dark / light", self)
        theme.triggered.connect(self._toggle_theme)
        toolbar.addAction(theme)
        on_top = QAction("On top", self, checkable=True)
        on_top.setChecked(True)
        on_top.toggled.connect(self._set_on_top)
        toolbar.addAction(on_top)
        mute = QAction("Mute TTS (Ctrl+M)", self)
        mute.triggered.connect(self.mute_tts)
        toolbar.addAction(mute)
        enable_speech = QAction("Enable TTS", self)
        enable_speech.triggered.connect(self.unmute_tts)
        toolbar.addAction(enable_speech)
        toolbar.addWidget(QLabel("  Opacity "))
        opacity = QComboBox()
        opacity.addItems(("100%", "90%", "80%", "70%"))
        opacity.currentTextChanged.connect(lambda value: self.setWindowOpacity(int(value[:-1]) / 100))
        toolbar.addWidget(opacity)

        self.body = QTabWidget()
        self.setCentralWidget(self.body)
        live = QWidget()
        live_layout = QVBoxLayout(live)

        devices = QFrame()
        device_form = QFormLayout(devices)
        self.output_combo = QComboBox()
        self.mic_combo = QComboBox()
        self.mic_check = QCheckBox("Include microphone")
        self.mic_check.toggled.connect(self.mic_combo.setEnabled)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_combo)
        refresh = QPushButton("Refresh devices")
        refresh.clicked.connect(self.refresh_devices)
        output_row.addWidget(refresh)
        device_form.addRow("Playback output", output_row)
        mic_row = QHBoxLayout()
        mic_row.addWidget(self.mic_check)
        mic_row.addWidget(self.mic_combo)
        device_form.addRow("Optional mic", mic_row)
        route = QPushButton("Create isolated Meet route")
        route.clicked.connect(self.create_route)
        device_form.addRow(route)
        self.route_label = QLabel("Route not created. No capture occurs on launch.")
        self.route_label.setWordWrap(True)
        device_form.addRow(self.route_label)
        live_layout.addWidget(devices)

        controls = QHBoxLayout()
        self.start_button = QPushButton("Start")
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.stop_button = QPushButton("Stop")
        self.clear_button = QPushButton("Delete / Clear meeting")
        for button, slot in (
            (self.start_button, self.start),
            (self.pause_button, self.pause),
            (self.resume_button, self.resume),
            (self.stop_button, self.stop),
            (self.clear_button, self.clear_meeting),
        ):
            button.clicked.connect(slot)
            controls.addWidget(button)
        live_layout.addLayout(controls)

        statuses = QFormLayout()
        self.state_label = QLabel("Idle — capture is off")
        self.capture_label = QLabel("Not capturing")
        self.status_label = QLabel("Local STT ready when started")
        self.topic_label = QLabel("Waiting for discussion")
        self.analysis_label = QLabel("Cloud analysis disabled")
        self.privacy_label = QLabel(
            "Provider privacy: cloud analysis disabled; local transcription stays local."
        )
        self.privacy_label.setWordWrap(True)
        self.usage_label = QLabel("Usage: 0 seconds")
        for name, label in (
            ("State", self.state_label),
            ("Capture", self.capture_label),
            ("STT / error", self.status_label),
            ("Topic", self.topic_label),
            ("Analysis", self.analysis_label),
            ("Privacy", self.privacy_label),
            ("Usage", self.usage_label),
        ):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            statuses.addRow(name, label)
        live_layout.addLayout(statuses)

        self.transcript = QPlainTextEdit(readOnly=True)
        self.transcript.document().setMaximumBlockCount(300)
        self.transcript.setPlaceholderText("Live local transcript (not retained by default)")
        live_layout.addWidget(self.transcript, 1)
        self.insights_host = QWidget()
        self.insights_layout = QVBoxLayout(self.insights_host)
        self.insights_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        insight_scroll = QScrollArea(widgetResizable=True)
        insight_scroll.setWidget(self.insights_host)
        live_layout.addWidget(insight_scroll, 2)
        self.body.addTab(live, "Meeting")

        settings = QWidget()
        form = QFormLayout(settings)
        self.stt_combo = QComboBox()
        self.stt_combo.addItems(("small", "base"))
        form.addRow("Local multilingual STT (English-first)", self.stt_combo)
        self.cloud_check = QCheckBox("I consent to sending transcript/project context to OpenRouter")
        self.cloud_check.toggled.connect(self._cloud_toggled)
        form.addRow(self.cloud_check)
        self.cloud_settings = QFrame()
        cloud = QFormLayout(self.cloud_settings)
        self.key_edit = QLineEdit(os.getenv("OPENROUTER_API_KEY", ""))
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Session only; environment fallback supported")
        self.model_edit = QLineEdit(os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL))
        self.confidence = QDoubleSpinBox()
        self.confidence.setRange(0, 1)
        self.confidence.setSingleStep(0.05)
        self.confidence.setValue(0.85)
        self.budget = self._money_spin(0.50, 1000)
        self.input_price = self._money_spin(DEFAULT_INPUT_PRICE, 100000)
        self.output_price = self._money_spin(DEFAULT_OUTPUT_PRICE, 100000)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(("Technical", "Requirements", "Client", "General"))
        for name, widget in (
            ("OpenRouter key", self.key_edit),
            ("Model", self.model_edit),
            ("Confidence", self.confidence),
            ("Budget USD", self.budget),
            ("Input $ / 1M tokens", self.input_price),
            ("Output $ / 1M tokens", self.output_price),
            ("Mode", self.mode_combo),
        ):
            cloud.addRow(name, widget)
        form.addRow(self.cloud_settings)
        self.project_edit = QTextEdit()
        self.project_edit.setPlaceholderText(
            f"Paste project context (maximum {self.PROJECT_LIMIT} characters)"
        )
        form.addRow("Project context", self.project_edit)
        load_project = QPushButton("Load local .txt / .md")
        load_project.clicked.connect(self.load_project)
        form.addRow(load_project)
        self.retain_check = QCheckBox("Retain transcript in a private app-owned SQLite file")
        form.addRow(self.retain_check)
        self.body.addTab(settings, "Analysis & storage")

        speech = QWidget()
        tts_form = QFormLayout(speech)
        self.tts_mode = QComboBox()
        for label, value in (
            ("Disabled", "disabled"),
            ("High priority", "high"),
            ("Questions", "questions"),
            ("Questions + concerns", "questions_concerns"),
            ("All insights", "all"),
        ):
            self.tts_mode.addItem(label, value)
        self.headphones_check = QCheckBox("I confirm I am wearing headphones")
        self.routing_check = QCheckBox("I verified private routing; selected output matches the route")
        self.tts_model = QLineEdit()
        self.tts_config = QLineEdit()
        self.tts_note = QLabel(
            "Piper voice model and config must be installed separately. TTS needs an isolated route."
        )
        self.tts_note.setWordWrap(True)
        for name, widget in (
            ("TTS mode", self.tts_mode),
            ("", self.headphones_check),
            ("", self.routing_check),
            ("Piper model path", self.tts_model),
            ("Piper config path", self.tts_config),
            ("", self.tts_note),
        ):
            tts_form.addRow(name, widget)
        self.body.addTab(speech, "Private TTS")

        summary = QWidget()
        summary_layout = QVBoxLayout(summary)
        self.summary_edit = QTextEdit(readOnly=True)
        self.summary_edit.setPlaceholderText(
            "Stop the meeting to prepare a summary. Cloud analysis must succeed first."
        )
        summary_layout.addWidget(self.summary_edit)
        save = QPushButton("Save final Markdown summary")
        save.clicked.connect(self.save_summary)
        export = QPushButton("Export retained transcript")
        export.clicked.connect(self.export_transcript)
        summary_layout.addWidget(save)
        summary_layout.addWidget(export)
        self.body.addTab(summary, "Summary")
        self._cloud_toggled(False)

    @staticmethod
    def _money_spin(value, maximum):
        spin = QDoubleSpinBox()
        spin.setDecimals(6)
        spin.setRange(0, maximum)
        spin.setValue(value)
        return spin

    def _shortcuts(self):
        for key, slot in (
            ("Ctrl+Return", self.start),
            ("Ctrl+P", self.pause),
            ("Ctrl+R", self.resume),
            ("Ctrl+.", self.stop),
            ("Ctrl+L", self.clear_meeting),
            ("Ctrl+M", self.mute_tts),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(slot)

    def _cloud_toggled(self, checked):
        self.cloud_settings.setEnabled(checked)
        self.analysis_label.setText(
            "Cloud analysis ready when started" if checked else "Cloud analysis disabled"
        )
        self.privacy_label.setText(
            "Provider privacy: OpenRouter requests deny provider data collection; "
            "review OpenRouter/provider policies before use."
            if checked
            else "Provider privacy: cloud analysis disabled; local transcription stays local."
        )

    def _set_on_top(self, enabled):
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        if was_visible:
            self.show()

    def _toggle_theme(self):
        dark = not bool(self.styleSheet())
        self.setStyleSheet(
            "QWidget { background:#202124; color:#eee; } QLineEdit,QTextEdit,QComboBox,"
            "QDoubleSpinBox,QSpinBox { background:#303134; }"
            if dark
            else ""
        )

    def _toggle_collapse(self):
        self.body.setVisible(not self.body.isVisible())
        self.resize(self.width(), self.minimumHeight() if self.body.isHidden() else 760)

    def refresh_devices(self):
        selected_output, selected_mic = self.output_combo.currentData(), self.mic_combo.currentData()
        try:
            outputs, microphones = audio.list_devices()
            outputs = [item for item in outputs if not item.name.startswith("meeting_copilot_")]
            self.output_combo.clear()
            self.mic_combo.clear()
            for item in outputs:
                self.output_combo.addItem(item.description, item.name)
            for item in microphones:
                self.mic_combo.addItem(item.description, item.name)
            for combo, wanted in ((self.output_combo, selected_output), (self.mic_combo, selected_mic)):
                index = combo.findData(wanted)
                if index >= 0:
                    combo.setCurrentIndex(index)
            self.status_label.setText("Devices refreshed; selected capture sources are unchanged.")
        except Exception:  # noqa: BLE001 -- audio backend failures must not crash the panel
            self.status_label.setText(
                "Audio devices unavailable. Install/configure PipeWire Pulse or PulseAudio."
            )
        self.mic_combo.setEnabled(self.mic_check.isChecked())

    def create_route(self):
        if self.route:
            QMessageBox.information(
                self, "Route exists", "Stop/clear this meeting before creating another route."
            )
            return
        output = self.output_combo.currentData()
        if not output:
            QMessageBox.warning(self, "No output", "Select a physical playback output first.")
            return
        try:
            route = audio.MeetingRoute()
            sink, monitor = route.create(output)
            self.route, self.route_output, self.route_monitor = route, output, monitor
            self.output_combo.setEnabled(False)
            self.route_label.setText(
                f"In pavucontrol, route the Meet/browser playback stream to “MeetingCopilot” "
                f"({sink}). Keep Meet microphone unchanged. Start will capture {monitor}; playback "
                f"continues separately through the selected output."
            )
        except Exception:  # noqa: BLE001 -- route cleanup is handled by MeetingRoute
            self.status_label.setText(
                "Could not create the isolated route; check the audio server and pavucontrol."
            )

    def _project_text(self):
        text = self.project_edit.toPlainText().strip()
        if len(text) > self.PROJECT_LIMIT:
            self.status_label.setText(f"Project context exceeds {self.PROJECT_LIMIT} characters.")
            return ""
        return text

    def load_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load project context", "", "Text (*.txt *.md)")
        if not path:
            return
        source = Path(path)
        if source.suffix.lower() not in (".txt", ".md"):
            QMessageBox.warning(self, "Unsupported file", "Choose a local .txt or .md file.")
            return
        try:
            if source.stat().st_size > self.PROJECT_LIMIT * 4:
                raise ValueError
            text = source.read_text(encoding="utf-8")
            if len(text) > self.PROJECT_LIMIT:
                raise ValueError
            self.project_edit.setPlainText(text)
        except (OSError, UnicodeError, ValueError):
            QMessageBox.warning(
                self, "Project context", f"File must be UTF-8 and at most {self.PROJECT_LIMIT} characters."
            )

    def _new_store(self):
        if not self.retain_check.isChecked():
            return LocalStore()
        location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        if not location:
            raise RuntimeError(
                "No platform user-data directory is available; transcript retention stays off."
            )
        root = Path(location) / "meeting-copilot" / "meetings"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        return LocalStore(root / f"meeting-{uuid.uuid4().hex}.sqlite", keep_transcript=True)

    def _make_tts(self):
        mode = self.tts_mode.currentData()
        if mode == "disabled":
            return None
        valid = (
            self.route
            and self.route_output == self.output_combo.currentData()
            and self.headphones_check.isChecked()
            and self.routing_check.isChecked()
        )
        if not valid:
            raise ValueError(
                "TTS requires headphones, verified private routing, and the route’s selected output."
            )
        model, config = Path(self.tts_model.text()), Path(self.tts_config.text())
        if not model.is_file() or not config.is_file():
            raise ValueError("Install and select the Piper model and config files.")
        from meeting_copilot.tts import PiperTTS

        return PiperTTS(
            str(model),
            str(config),
            self.route_output,
            mode=mode,
            on_status=lambda message: self.worker_event("status", message),
        )

    def start(self):
        if self.session:
            return
        if not self.route or not self.route_monitor:
            QMessageBox.warning(
                self, "Create route first", "Create the isolated Meet route, then route Meet in pavucontrol."
            )
            return
        project = self._project_text()
        if self.project_edit.toPlainText().strip() and not project:
            return
        key = self.key_edit.text().strip()
        if self.cloud_check.isChecked() and not key:
            QMessageBox.warning(
                self, "No key", "Cloud analysis consent is on, but no session key is available."
            )
            return
        try:
            provider = (
                OpenRouterLLM(
                    key,
                    self.model_edit.text().strip() or DEFAULT_MODEL,
                    self.budget.value(),
                    self.input_price.value(),
                    self.output_price.value(),
                )
                if self.cloud_check.isChecked()
                else None
            )
            store = self._new_store()
            try:
                self.tts = self._make_tts()
                self.session = Session(
                    self.worker_event,
                    model=self.stt_combo.currentText(),
                    llm=provider,
                    mode=self.mode_combo.currentText(),
                    project=project,
                    store=store,
                    threshold=self.confidence.value(),
                    tts=self.tts,
                )
                self.session.start(
                    self.route_monitor, self.mic_combo.currentData() if self.mic_check.isChecked() else None
                )
            except Exception:
                store.delete()
                raise
            self._set_running(True)
        except Exception as exc:  # noqa: BLE001 -- keep configuration failures in the UI
            self.session = None
            self._release_tts()
            self.status_label.setText(str(exc))

    def pause(self):
        if self.session:
            self.session.pause()
            self.pause_button.setEnabled(False)
            self.resume_button.setEnabled(True)

    def resume(self):
        if self.session:
            self.session.resume()
            self.pause_button.setEnabled(True)
            self.resume_button.setEnabled(False)

    def stop(self):
        if self.session:
            self.mute_tts()
            self.session.stop()
            self.stop_button.setEnabled(False)
            self.pause_button.setEnabled(False)
            self.resume_button.setEnabled(False)

    def unmute_tts(self):
        if self.tts:
            self.tts.unmute()
            self.status_label.setText("TTS enabled for the selected filtering mode.")

    def mute_tts(self):
        if self.tts:
            self.tts.mute()
            self.status_label.setText("TTS muted immediately.")

    def worker_event(self, kind, payload):
        self.bridge.event.emit(kind, payload)

    def _on_event(self, kind, payload):
        if kind == "state":
            self.state_label.setText(str(payload))
            if str(payload).startswith("Stopped"):
                self._finished()
        elif kind == "capture":
            source = str(payload).split(":", 1)[0]
            self.capture_states[source] = str(payload)
            self.capture_label.setText("\n".join(self.capture_states.values()))
        elif kind == "status":
            self.status_label.setText(str(payload))
        elif kind == "analysis_status":
            self.analysis_label.setText(str(payload))
        elif kind == "topic":
            self.topic_label.setText(str(payload))
        elif kind == "transcript":
            self.transcript.appendPlainText(f"{payload.get('source', 'Meeting')}: {payload.get('text', '')}")
        elif kind == "insight":
            self._add_insight(payload)
        elif kind == "summary":
            self.summary = str(payload)
            self.summary_edit.setPlainText(self.summary)
        elif kind == "usage":
            self._show_usage(payload)

    def _add_insight(self, insight):
        while self.insights_layout.count() >= 8:
            old = self.insights_layout.takeAt(0).widget()
            if old:
                old.deleteLater()
        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setProperty("insight", insight)
        card.setToolTip(
            html.escape(
                f"Reason: {insight.get('reason', '')}\nEvidence: {', '.join(insight.get('evidence_ids', []))}"
            )
        )
        layout = QVBoxLayout(card)
        layout.addWidget(QLabel(f"{insight.get('type', 'INSIGHT')} · {insight.get('priority', '')}"))
        text = QLabel(str(insight.get("text", "")))
        text.setTextFormat(Qt.TextFormat.PlainText)
        text.setWordWrap(True)
        layout.addWidget(text)
        reason = QLabel(
            f"Reason: {insight.get('reason', '')}\nEvidence: {', '.join(insight.get('evidence_ids', []))}"
        )
        reason.setTextFormat(Qt.TextFormat.PlainText)
        reason.setWordWrap(True)
        layout.addWidget(reason)
        buttons = QHBoxLayout()
        for title, action in (
            ("Dismiss", "dismissed"),
            ("Save", "saved"),
            ("Useful", "useful"),
            ("Not useful", "not_useful"),
        ):
            button = QPushButton(title)
            button.clicked.connect(
                lambda _=False, item=insight, value=action, widget=card: self._feedback(item, value, widget)
            )
            buttons.addWidget(button)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(str(insight.get("text", ""))))
        explain = QPushButton("Explain")
        explain.clicked.connect(lambda: self._explain(insight))
        buttons.addWidget(copy)
        buttons.addWidget(explain)
        layout.addLayout(buttons)
        self.insights_layout.addWidget(card)

    def _explain(self, insight):
        box = QMessageBox(self)
        box.setWindowTitle("Insight evidence")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(
            f"Reason: {insight.get('reason', '')}\nEvidence: {', '.join(insight.get('evidence_ids', []))}"
        )
        box.exec()

    def _feedback(self, insight, action, card):
        if self.session:
            try:
                self.session.feedback(insight.get("id"), action)
            except Exception:  # noqa: BLE001 -- feedback failure must not stop capture
                self.status_label.setText("Could not store insight feedback.")
        if action == "saved":
            self.status_label.setText("Marked for the final summary; export the summary to keep it.")
        if action == "dismissed":
            card.deleteLater()

    def _usage_tick(self):
        if not self.session or self._usage_busy:
            return
        self._usage_busy = True
        session = self.session

        def read_stats():
            try:
                self.worker_event("usage", session.stats())
            finally:
                self._usage_busy = False

        threading.Thread(target=read_stats, daemon=True).start()

    def _show_usage(self, stats):
        seconds = stats.get("audio_seconds", 0)
        cost = stats.get("estimated_usd", 0)
        self.usage_label.setText(
            f"Usage: {seconds:.0f}s audio · {stats.get('calls', 0)} cloud calls · ${cost:.4f}"
            f" · {stats.get('analysis_failures', 0)} analysis failures"
            f" · {stats.get('pending_analysis_entries', 0)} pending entries"
        )

    def _finished(self):
        self._set_running(False)
        self.start_button.setEnabled(False)
        self._release_route()
        self._release_tts()
        if self._clear_after_stop:
            self._clear_after_stop = False
            self._clear_now()
        if self._close_after_stop:
            self._close_after_stop = False
            QTimer.singleShot(0, self.close)

    def _set_running(self, running):
        self.start_button.setEnabled(not running)
        self.pause_button.setEnabled(running)
        self.resume_button.setEnabled(False)
        self.stop_button.setEnabled(running)
        self.output_combo.setEnabled(not running and not self.route)
        self.mic_check.setEnabled(not running)
        self.stt_combo.setEnabled(not running)
        self.retain_check.setEnabled(not running)
        self.cloud_check.setEnabled(not running)
        self.cloud_settings.setEnabled(not running and self.cloud_check.isChecked())
        self.project_edit.setEnabled(not running)
        self.tts_mode.setEnabled(not running)

    def save_summary(self):
        if not self.summary:
            QMessageBox.information(self, "Summary", "Stop the meeting and wait for the final summary first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save summary", "meeting-summary.md", "Markdown (*.md)")
        if path:
            try:
                write_private(path, self.summary)
            except OSError:
                QMessageBox.warning(self, "Summary", "Could not save the summary.")

    def export_transcript(self):
        store = getattr(self.session, "store", None)
        if not store or not store.keep_transcript:
            QMessageBox.information(
                self, "Transcript", "Transcript retention was not enabled for this meeting."
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export transcript", "meeting-transcript.txt", "Text (*.txt)"
        )
        if path:
            try:
                write_private(path, store.export_transcript())
            except OSError:
                QMessageBox.warning(self, "Transcript", "Could not export the transcript.")

    def clear_meeting(self):
        if (
            QMessageBox.question(
                self,
                "Delete meeting",
                "Delete this meeting’s in-app transcript, insights, summary, "
                "feedback, and app-owned database? Saved exports and user recordings are never deleted.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        if self.session and not self._session_finished():
            self._clear_after_stop = True
            self.stop()
            return
        self._clear_now()

    def _clear_now(self):
        store = getattr(self.session, "store", None)
        if store:
            try:
                store.delete()
            except Exception:  # noqa: BLE001 -- storage implementations share no exception base
                self.status_label.setText("Could not delete the app-owned meeting database.")
                return
        self.session = None
        self.summary = ""
        self.transcript.clear()
        self.summary_edit.clear()
        self.project_edit.clear()
        self.usage_label.setText("Usage: 0 seconds")
        while self.insights_layout.count():
            widget = self.insights_layout.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        self.state_label.setText("Idle — capture is off")
        self.capture_states.clear()
        self.capture_label.setText("Not capturing")
        self.topic_label.setText("Waiting for discussion")
        self.analysis_label.setText(
            "Cloud analysis ready when started" if self.cloud_check.isChecked() else "Cloud analysis disabled"
        )
        self._release_route()
        self._release_tts()
        self._set_running(False)

    @staticmethod
    def _flag_set(value):
        return value.is_set() if hasattr(value, "is_set") else bool(value)

    def _session_finished(self):
        return self._flag_set(getattr(self.session, "finished", False))

    def _release_route(self):
        if self.route:
            try:
                self.route.close()
            except Exception:  # noqa: BLE001 -- shutdown continues after backend loss
                self.status_label.setText("Audio route cleanup failed; check pavucontrol.")
        self.route = None
        self.route_output = self.route_monitor = ""
        self.route_label.setText("Route not created. No capture occurs on launch.")
        self.output_combo.setEnabled(True)

    def _release_tts(self):
        if self.tts:
            try:
                self.tts.close()
            except Exception:  # noqa: BLE001 -- shutdown continues after TTS loss
                self.status_label.setText("TTS cleanup failed.")
        self.tts = None

    def closeEvent(self, event):
        if self.session and not self._session_finished():
            if not self._flag_set(getattr(self.session, "stopping", False)):
                answer = QMessageBox.question(
                    self,
                    "Meeting active",
                    "Stop capture, drain queued transcription, finish the summary, then close?",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
                self._close_after_stop = True
                self.stop()
            else:
                self._close_after_stop = True
            event.ignore()
            return
        self._release_route()
        self._release_tts()
        store = getattr(self.session, "store", None)
        if store:
            try:
                store.close()
            except Exception:  # noqa: BLE001 -- window shutdown cannot recover storage
                self.status_label.setText("Meeting storage could not be closed cleanly.")
        event.accept()


def main():
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("MeetingCopilot")
    app.setOrganizationName("MeetingCopilot")
    panel = MeetingCopilotPanel()
    panel.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
