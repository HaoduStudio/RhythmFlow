from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QSettings, QSignalBlocker, Qt, QThread
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSlider,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from rhythmflow.config import (
    APP_AUTHOR,
    APP_NAME,
    DEFAULT_LANGUAGE,
    DEFAULT_ORIGINAL_VOLUME,
    DEFAULT_OUTPUT_PATTERN,
    DEFAULT_REFERENCE_VOLUME,
    ORG_NAME,
    REPOSITORY_URL,
    SettingsKeys,
)
from rhythmflow import __version__
from rhythmflow.core.pipeline import ProcessJob, build_output_path
from rhythmflow.core.segmented_alignment import ReferenceSegment
from rhythmflow.logging_setup import record_metric
from rhythmflow.ui.i18n import LANGUAGE_NAMES, tr
from rhythmflow.ui.review_dialog import ReviewDialog, ReviewSegment, ReviewSegmentAdjustment
from rhythmflow.ui.widgets import FileDropList
from rhythmflow.ui.workers import AnalyzeWorker, ProcessWorker


logger = logging.getLogger(__name__)

REVIEW_CONFIRMED_ROLE = int(Qt.ItemDataRole.UserRole)
REVIEW_REQUIRED_ROLE = int(Qt.ItemDataRole.UserRole) + 1
PREVIEW_DURATION_S = 8.0
LANGUAGE_COMBO_MIN_WIDTH = 136
SUSPICIOUS_GLOBAL_OFFSET_S = 15.0


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings(ORG_NAME, APP_NAME)
        self._thread: QThread | None = None
        self._worker = None
        self.language = self._saved_language()
        self.setWindowTitle("RhythmFlow")
        self.resize(1160, 760)
        self._build_ui()
        self._restore_settings()
        logger.info("Main window initialized with language=%s", self.language)

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        top_row = QHBoxLayout()
        title = QLabel("RhythmFlow")
        title.setObjectName("TitleLabel")
        title.setStyleSheet("font-size: 28px; font-weight: 700;")
        top_row.addWidget(title)
        top_row.addStretch(1)
        self.about_button = QPushButton()
        self.about_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))
        self.about_button.setFixedSize(34, 34)
        self.about_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.about_button.clicked.connect(self._show_about_dialog)
        top_row.addWidget(self.about_button)
        self.language_label = QLabel()
        self.language_combo = QComboBox()
        self._configure_language_combo()
        for code, name in LANGUAGE_NAMES.items():
            self.language_combo.addItem(name, code)
        self.language_combo.currentIndexChanged.connect(self._change_language)
        top_row.addWidget(self.language_label)
        top_row.addWidget(self.language_combo)
        root.addLayout(top_row)

        content = QGridLayout()
        content.setColumnStretch(0, 1)
        content.setColumnStretch(1, 1)
        content.setRowStretch(0, 0)
        content.setRowStretch(1, 1)
        content.setRowMinimumHeight(1, 170)
        root.addLayout(content, stretch=1)

        self.inputs_box = QGroupBox()
        inputs_layout = QVBoxLayout(self.inputs_box)
        self.video_list = FileDropList()
        self.video_list.paths_changed.connect(self._sync_table_rows)
        self.handcam_label = QLabel()
        inputs_layout.addWidget(self.handcam_label)
        inputs_layout.addWidget(self.video_list)

        video_buttons = QHBoxLayout()
        self.add_videos_button = QPushButton()
        self.remove_videos_button = QPushButton()
        self.clear_videos_button = QPushButton()
        self.add_videos_button.clicked.connect(self._add_videos)
        self.remove_videos_button.clicked.connect(self.video_list.remove_selected)
        self.clear_videos_button.clicked.connect(self.video_list.clear_paths)
        video_buttons.addWidget(self.add_videos_button)
        video_buttons.addWidget(self.remove_videos_button)
        video_buttons.addWidget(self.clear_videos_button)
        video_buttons.addStretch(1)
        inputs_layout.addLayout(video_buttons)

        form = QFormLayout()
        self.reference_edit = QLineEdit()
        self.reference_button = QPushButton()
        self.reference_button.clicked.connect(self._browse_reference)
        reference_row = QHBoxLayout()
        reference_row.addWidget(self.reference_edit, stretch=1)
        reference_row.addWidget(self.reference_button)
        self.reference_form_label = QLabel()
        form.addRow(self.reference_form_label, reference_row)

        self.output_edit = QLineEdit()
        self.output_button = QPushButton()
        self.output_button.clicked.connect(self._browse_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit, stretch=1)
        output_row.addWidget(self.output_button)
        self.output_form_label = QLabel()
        form.addRow(self.output_form_label, output_row)

        self.pattern_edit = QLineEdit(DEFAULT_OUTPUT_PATTERN)
        self.pattern_form_label = QLabel()
        form.addRow(self.pattern_form_label, self.pattern_edit)
        inputs_layout.addLayout(form)
        content.addWidget(self.inputs_box, 0, 0)

        self.options_box = QGroupBox()
        options_layout = QFormLayout(self.options_box)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("", "accurate")
        self.mode_combo.addItem("", "fast")
        self.mode_form_label = QLabel()
        options_layout.addRow(self.mode_form_label, self.mode_combo)

        self.original_slider, self.original_label = self._volume_slider(DEFAULT_ORIGINAL_VOLUME)
        self.reference_slider, self.reference_label = self._volume_slider(DEFAULT_REFERENCE_VOLUME)
        self.original_form_label = QLabel()
        self.reference_volume_form_label = QLabel()
        options_layout.addRow(self.original_form_label, self._slider_row(self.original_slider, self.original_label))
        options_layout.addRow(
            self.reference_volume_form_label,
            self._slider_row(self.reference_slider, self.reference_label),
        )

        self.analyze_button = QPushButton()
        self.process_button = QPushButton()
        self.analyze_button.clicked.connect(self._start_analyze)
        self.process_button.clicked.connect(self._start_process)
        actions = QHBoxLayout()
        actions.addWidget(self.analyze_button)
        actions.addWidget(self.process_button)
        actions.addStretch(1)
        self.run_form_label = QLabel()
        options_layout.addRow(self.run_form_label, actions)
        content.addWidget(self.options_box, 0, 1)

        self.table_box = QGroupBox()
        table_layout = QVBoxLayout(self.table_box)
        self.table = QTableWidget(0, 8)
        self._configure_table()
        self.table.setMinimumHeight(170)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table_layout.addWidget(self.table)
        content.addWidget(self.table_box, 1, 0, 1, 2)

        self.bottom_box = QGroupBox()
        bottom_layout = QVBoxLayout(self.bottom_box)
        self.progress = QProgressBar()
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(80)
        self.log.setMaximumHeight(120)
        bottom_layout.addWidget(self.progress)
        bottom_layout.addWidget(self.log)
        self.bottom_box.setMaximumHeight(190)
        root.addWidget(self.bottom_box)

        self.setCentralWidget(central)
        self._retranslate_ui()

    def _configure_table(self) -> None:
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(88)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 118), (2, 96), (3, 124), (4, 104), (5, 118), (6, 96), (7, 112)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(column, width)

    def _configure_language_combo(self) -> None:
        self.language_combo.setMinimumWidth(LANGUAGE_COMBO_MIN_WIDTH)
        self.language_combo.setMinimumContentsLength(max(len(name) for name in LANGUAGE_NAMES.values()))
        self.language_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.language_combo.setStyleSheet(
            """
            QComboBox {
                padding-left: 12px;
                padding-right: 28px;
            }
            QComboBox QAbstractItemView::item {
                min-height: 32px;
                padding-left: 12px;
                padding-right: 12px;
            }
            """
        )
        view = QListView(self.language_combo)
        view.setMinimumWidth(LANGUAGE_COMBO_MIN_WIDTH)
        view.setTextElideMode(Qt.TextElideMode.ElideNone)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.language_combo.setView(view)

    def _show_about_dialog(self) -> None:
        message = QMessageBox(self)
        message.setWindowTitle(tr(self.language, "about_title"))
        message.setIcon(QMessageBox.Icon.Information)
        message.setText(
            "<dl>"
            f"<dt><b>{tr(self.language, 'about_app_name')}</b></dt><dd>{APP_NAME}</dd>"
            f"<dt><b>{tr(self.language, 'about_version')}</b></dt><dd>{__version__}</dd>"
            f"<dt><b>{tr(self.language, 'about_author')}</b></dt><dd>{APP_AUTHOR}</dd>"
            f"<dt><b>{tr(self.language, 'about_repository')}</b></dt>"
            f"<dd><a href=\"{REPOSITORY_URL}\">{REPOSITORY_URL}</a></dd>"
            "</dl>"
        )
        message.setTextFormat(Qt.TextFormat.RichText)
        message.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        message.setStandardButtons(QMessageBox.StandardButton.Ok)
        message.exec()

    def _restore_settings(self) -> None:
        language_index = self.language_combo.findData(self.language)
        if language_index >= 0:
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(language_index)
            self.language_combo.blockSignals(False)
        self.output_edit.setText(
            self.settings.value(SettingsKeys.OUTPUT_DIR, str(Path.cwd() / "output"), str)
        )
        self.pattern_edit.setText(
            self.settings.value(SettingsKeys.OUTPUT_PATTERN, DEFAULT_OUTPUT_PATTERN, str)
        )
        self.original_slider.setValue(
            int(self.settings.value(SettingsKeys.ORIGINAL_VOLUME, DEFAULT_ORIGINAL_VOLUME))
        )
        self.reference_slider.setValue(
            int(self.settings.value(SettingsKeys.REFERENCE_VOLUME, DEFAULT_REFERENCE_VOLUME))
        )
        mode = self.settings.value(SettingsKeys.CUT_MODE, "accurate", str)
        mode_index = self.mode_combo.findData(mode)
        if mode_index >= 0:
            self.mode_combo.setCurrentIndex(mode_index)

    def closeEvent(self, event) -> None:
        logger.info("Main window closing")
        self._save_settings()
        super().closeEvent(event)

    def _save_settings(self) -> None:
        self.settings.setValue(SettingsKeys.LANGUAGE, self.language)
        self.settings.setValue(SettingsKeys.OUTPUT_DIR, self.output_edit.text())
        self.settings.setValue(SettingsKeys.OUTPUT_PATTERN, self.pattern_edit.text())
        self.settings.setValue(SettingsKeys.ORIGINAL_VOLUME, self.original_slider.value())
        self.settings.setValue(SettingsKeys.REFERENCE_VOLUME, self.reference_slider.value())
        self.settings.setValue(SettingsKeys.CUT_MODE, self.mode_combo.currentData())

    def _volume_slider(self, value: int) -> tuple[QSlider, QLabel]:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 200)
        slider.setSingleStep(5)
        slider.setPageStep(10)
        label = QLabel()
        slider.valueChanged.connect(lambda new_value: label.setText(f"{new_value}%"))
        slider.setValue(value)
        return slider, label

    def _slider_row(self, slider: QSlider, label: QLabel) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(slider, stretch=1)
        layout.addWidget(label)
        return widget

    def _saved_language(self) -> str:
        language = self.settings.value(SettingsKeys.LANGUAGE, DEFAULT_LANGUAGE, str)
        if language not in LANGUAGE_NAMES:
            return DEFAULT_LANGUAGE
        return language

    def _change_language(self) -> None:
        language = self.language_combo.currentData()
        if language not in LANGUAGE_NAMES:
            return
        self.language = str(language)
        self.settings.setValue(SettingsKeys.LANGUAGE, self.language)
        self._retranslate_ui()
        logger.info("Language changed to %s", self.language)

    def _retranslate_ui(self) -> None:
        self.about_button.setAccessibleName(tr(self.language, "about_button"))
        self.about_button.setToolTip(tr(self.language, "about_button"))
        self.language_label.setText(tr(self.language, "language"))
        self.inputs_box.setTitle(tr(self.language, "inputs"))
        self.handcam_label.setText(tr(self.language, "handcam_videos"))
        self.add_videos_button.setText(tr(self.language, "add"))
        self.remove_videos_button.setText(tr(self.language, "remove"))
        self.clear_videos_button.setText(tr(self.language, "clear"))
        self.reference_button.setText(tr(self.language, "browse"))
        self.output_button.setText(tr(self.language, "browse"))
        self.reference_form_label.setText(tr(self.language, "reference_audio"))
        self.output_form_label.setText(tr(self.language, "output_directory"))
        self.pattern_form_label.setText(tr(self.language, "filename_pattern"))
        self.options_box.setTitle(tr(self.language, "options"))
        self.mode_form_label.setText(tr(self.language, "cut_mode"))
        self.mode_combo.setItemText(0, tr(self.language, "mode_accurate"))
        self.mode_combo.setItemText(1, tr(self.language, "mode_fast"))
        self.original_form_label.setText(tr(self.language, "original_audio"))
        self.reference_volume_form_label.setText(tr(self.language, "reference_audio"))
        self.analyze_button.setText(tr(self.language, "analyze"))
        self.process_button.setText(tr(self.language, "process"))
        self.run_form_label.setText(tr(self.language, "run"))
        self.table_box.setTitle(tr(self.language, "alignment"))
        self.table.setHorizontalHeaderLabels(
            [
                tr(self.language, "table_file"),
                tr(self.language, "table_offset"),
                tr(self.language, "table_confidence"),
                tr(self.language, "table_nudge"),
                tr(self.language, "table_final"),
                tr(self.language, "table_smart_trim"),
                tr(self.language, "table_ai_confidence"),
                tr(self.language, "table_review"),
            ]
        )
        self.bottom_box.setTitle(tr(self.language, "progress"))
        self.table.resizeRowsToContents()

    def _add_videos(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            tr(self.language, "add_videos_dialog"),
            "",
            tr(self.language, "video_filter"),
        )
        self.video_list.add_paths(files)
        if files:
            logger.info("Selected %d video file(s)", len(files))

    def _browse_reference(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            tr(self.language, "choose_reference_dialog"),
            "",
            tr(self.language, "audio_filter"),
        )
        if file_path:
            self.reference_edit.setText(file_path)
            logger.info("Reference audio selected: %s", file_path)

    def _browse_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            tr(self.language, "choose_output_dialog"),
            self.output_edit.text(),
        )
        if directory:
            self.output_edit.setText(directory)
            logger.info("Output directory selected: %s", directory)

    def _sync_table_rows(self) -> None:
        paths = self.video_list.paths()
        self.table.setRowCount(len(paths))
        for row, path in enumerate(paths):
            file_item = self.table.item(row, 0) or QTableWidgetItem()
            path_changed = file_item.data(Qt.ItemDataRole.UserRole) != path
            file_item.setText(Path(path).name)
            file_item.setToolTip(path)
            file_item.setData(Qt.ItemDataRole.UserRole, path)
            self.table.setItem(row, 0, file_item)

            for column in (1, 2, 4, 5, 6, 7):
                if self.table.item(row, column) is None:
                    self.table.setItem(row, column, QTableWidgetItem(""))
                if path_changed:
                    item = self.table.item(row, column)
                    item.setText("")
                    item.setData(Qt.ItemDataRole.UserRole, None)
                    if column == 7:
                        item.setData(REVIEW_REQUIRED_ROLE, False)
                        item.setData(REVIEW_CONFIRMED_ROLE, False)
                    item.setToolTip("")
                    item.setBackground(QBrush())

            spin = self.table.cellWidget(row, 3)
            if spin is None:
                spin = QDoubleSpinBox()
                spin.setRange(-60.0, 60.0)
                spin.setSingleStep(0.01)
                spin.setDecimals(3)
                spin.valueChanged.connect(self._on_nudge_changed)
                self.table.setCellWidget(row, 3, spin)
            if path_changed:
                spin.blockSignals(True)
                spin.setValue(0.0)
                spin.blockSignals(False)
            self._update_final_offset(row)

    def _set_row_status(self, row: int, ok: bool, message: str = "", review: bool = False) -> None:
        if review:
            color = QColor("#78350f")
        else:
            color = QColor("#1b5e20") if ok else QColor("#7f1d1d")
        for column in range(self.table.columnCount()):
            item = self.table.item(row, column)
            if item:
                item.setBackground(color)
                if message:
                    item.setToolTip(message)

    def _start_analyze(self) -> None:
        videos = self.video_list.paths()
        reference = self.reference_edit.text().strip()
        logger.info("Analyze requested for %d video(s)", len(videos))
        if not videos:
            self._warn(tr(self.language, "warn_add_video"))
            return
        if not reference:
            self._warn(tr(self.language, "warn_choose_reference"))
            return
        self._sync_table_rows()
        self.progress.setValue(0)
        self._append_log(tr(self.language, "starting_analysis"))
        record_metric("rhythmflow.ui.analysis.requested", 1)
        worker = AnalyzeWorker(videos, reference, self.language)
        worker.result.connect(self._handle_analyze_result)
        self._run_worker(worker, on_finished=self._prompt_review_if_needed)

    def _handle_analyze_result(self, row: int, result: object) -> None:
        data = dict(result)
        if row >= self.table.rowCount():
            logger.warning("Ignoring analysis result for missing row %d", row)
            return
        if not data.get("ok"):
            error = data.get("error", tr(self.language, "error"))
            logger.error("Analysis row %d failed: %s", row, error)
            self.table.item(row, 1).setText(tr(self.language, "error"))
            self.table.item(row, 2).setText("")
            self.table.item(row, 4).setText("")
            self.table.item(row, 5).setText("")
            self.table.item(row, 5).setData(Qt.ItemDataRole.UserRole, None)
            self.table.item(row, 6).setText("")
            self.table.item(row, 7).setText("")
            self.table.item(row, 7).setData(REVIEW_REQUIRED_ROLE, False)
            self.table.item(row, 7).setData(REVIEW_CONFIRMED_ROLE, False)
            self._set_row_status(row, False, str(error))
            return
        offset = float(data["offset_s"])
        confidence = float(data["confidence"])
        trim_s = float(data.get("smart_trim_s") or 0.0)
        trim_count = int(data.get("smart_trim_count") or 0)
        smart_confidence = data.get("smart_confidence")
        needs_review = bool(data.get("needs_review"))
        plan = data.get("alignment_plan")
        if abs(offset) >= SUSPICIOUS_GLOBAL_OFFSET_S and not (
            isinstance(plan, dict) and plan.get("method") == "segmented"
        ):
            needs_review = True
        self.table.item(row, 1).setText(f"{offset:.3f}")
        self.table.item(row, 1).setData(Qt.ItemDataRole.UserRole, offset)
        self.table.item(row, 2).setText(f"{confidence:.2f}")
        self.table.item(row, 5).setText(f"{trim_s:.2f}s / {trim_count}")
        self.table.item(row, 5).setData(Qt.ItemDataRole.UserRole, plan)
        self.table.item(row, 6).setText("-" if smart_confidence is None else f"{float(smart_confidence):.2f}")
        self.table.item(row, 7).setText(
            tr(self.language, "review_required") if needs_review else tr(self.language, "review_ok")
        )
        self.table.item(row, 7).setData(REVIEW_REQUIRED_ROLE, needs_review)
        self.table.item(row, 7).setData(REVIEW_CONFIRMED_ROLE, not needs_review)
        tooltip = self._plan_tooltip(plan)
        self._set_row_status(row, True, tooltip, review=needs_review)
        self._update_final_offset(row)
        logger.info(
            "Analysis row %d updated: offset=%.3f confidence=%.2f review=%s",
            row,
            offset,
            confidence,
            needs_review,
        )

    def _start_process(self) -> None:
        reference = self.reference_edit.text().strip()
        output_dir = self.output_edit.text().strip()
        logger.info("Process requested for %d table row(s)", self.table.rowCount())
        if not reference:
            self._warn(tr(self.language, "warn_choose_reference"))
            return
        if not output_dir:
            self._warn(tr(self.language, "warn_choose_output"))
            return
        if self._unconfirmed_review_rows():
            self._warn(tr(self.language, "warn_review_required"))
            self._open_review_dialog()
            return
        jobs: list[ProcessJob] = []
        for row in range(self.table.rowCount()):
            file_item = self.table.item(row, 0)
            detected_item = self.table.item(row, 1)
            final_item = self.table.item(row, 4)
            plan_item = self.table.item(row, 5)
            if not file_item or not detected_item or detected_item.text() in {"", tr(self.language, "error")}:
                self._warn(tr(self.language, "warn_analyze_all"))
                return
            video_path = file_item.data(Qt.ItemDataRole.UserRole)
            offset = float(final_item.text())
            plan = plan_item.data(Qt.ItemDataRole.UserRole) if plan_item else None
            video_start_s, duration_s, video_segments, reference_segments = self._job_plan_values(row, plan)
            output_path = build_output_path(
                video_path,
                output_dir,
                self.pattern_edit.text(),
                row + 1,
            )
            jobs.append(
                ProcessJob(
                    video_path=video_path,
                    reference_audio_path=reference,
                    output_path=output_path,
                    offset_s=offset,
                    original_volume=self.original_slider.value() / 100,
                    reference_volume=self.reference_slider.value() / 100,
                    mode=str(self.mode_combo.currentData()),
                    video_start_s=video_start_s,
                    duration_s=duration_s,
                    video_segments=video_segments,
                    reference_segments=reference_segments,
                )
            )
        if not jobs:
            self._warn(tr(self.language, "warn_add_analyze"))
            return
        self._save_settings()
        self.progress.setValue(0)
        self._append_log(tr(self.language, "starting_processing"))
        record_metric("rhythmflow.ui.processing.requested", 1)
        worker = ProcessWorker(jobs, self.language)
        worker.file_started.connect(
            lambda path: self._append_log(tr(self.language, "processing_path", path=path))
        )
        worker.file_finished.connect(
            lambda _path, out: self._append_log(tr(self.language, "output_path", path=out))
        )
        self._run_worker(worker)

    def _run_worker(self, worker, on_finished=None) -> None:
        logger.info("Starting worker: %s", worker.__class__.__name__)
        self._set_busy(True)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.progress.setValue)
        worker.log.connect(self._append_log)
        worker.error.connect(lambda message: self._append_log(message, logging.ERROR))
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._set_busy(False))
        thread.finished.connect(lambda: setattr(self, "_thread", None))
        thread.finished.connect(lambda: setattr(self, "_worker", None))
        if on_finished is not None:
            thread.finished.connect(on_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _set_busy(self, busy: bool) -> None:
        for widget in (
            self.video_list,
            self.add_videos_button,
            self.remove_videos_button,
            self.clear_videos_button,
            self.reference_edit,
            self.reference_button,
            self.output_edit,
            self.output_button,
            self.pattern_edit,
            self.mode_combo,
            self.original_slider,
            self.reference_slider,
            self.analyze_button,
            self.process_button,
            self.language_combo,
        ):
            widget.setEnabled(not busy)

    def _update_final_offset(self, row: int) -> None:
        if row >= self.table.rowCount():
            return
        detected_item = self.table.item(row, 1)
        final_item = self.table.item(row, 4)
        spin = self.table.cellWidget(row, 3)
        if not detected_item or not final_item or not isinstance(spin, QDoubleSpinBox):
            return
        detected = detected_item.data(Qt.ItemDataRole.UserRole)
        if detected is None:
            detected = 0.0
        final_item.setText(f"{float(detected) + spin.value():.3f}")

    def _on_nudge_changed(self) -> None:
        sender = self.sender()
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, 3) is sender:
                self._update_final_offset(row)
                self._mark_review_dirty(row)
                return

    def _prompt_review_if_needed(self) -> None:
        rows = self._unconfirmed_review_rows()
        if not rows:
            return
        logger.warning("Review required for %d row(s)", len(rows))
        QMessageBox.warning(
            self,
            tr(self.language, "warning_title"),
            tr(self.language, "review_prompt", count=len(rows)),
        )
        self._open_review_dialog()

    def _open_review_dialog(self) -> None:
        segments = self._review_segments()
        if not segments:
            return
        logger.info("Opening review dialog with %d segment(s)", len(segments))
        dialog = ReviewDialog(segments, language=self.language, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            logger.info("Review dialog dismissed")
            return
        self._apply_review_adjustments(dialog.adjusted_segments_by_row)
        for row in dialog.confirmed_rows:
            self._mark_review_confirmed(row)
        logger.info("Review confirmed for %d row(s)", len(dialog.confirmed_rows))

    def _review_segments(self) -> list[ReviewSegment]:
        reference = self.reference_edit.text().strip()
        segments: list[ReviewSegment] = []
        for row in self._unconfirmed_review_rows():
            segments.extend(self._review_segments_for_row(row, reference))
        return segments

    def _review_segments_for_row(self, row: int, reference: str) -> list[ReviewSegment]:
        file_item = self.table.item(row, 0)
        final_item = self.table.item(row, 4)
        plan_item = self.table.item(row, 5)
        confidence_item = self.table.item(row, 2)
        if not file_item or not final_item:
            return []

        video_path = str(file_item.data(Qt.ItemDataRole.UserRole) or "")
        if not video_path or not reference:
            return []

        plan = plan_item.data(Qt.ItemDataRole.UserRole) if plan_item else None
        file_name = Path(video_path).name
        note = self._review_note(plan, confidence_item.text() if confidence_item else "")
        if isinstance(plan, dict) and plan.get("method") == "segmented":
            reference_segments = self._parse_plan_segments(plan.get("reference_segments"))
            video_segments = self._parse_plan_segments(plan.get("video_segments"))
            if reference_segments:
                result: list[ReviewSegment] = []
                nudge = self._row_nudge(row)
                video_cursor = max(0.0, float(plan.get("video_start_s") or 0.0) + nudge)
                has_paired_video_segments = len(video_segments) == len(reference_segments)
                for index, reference_segment in enumerate(reference_segments, start=1):
                    ref_start = reference_segment.start_s
                    ref_end = reference_segment.end_s
                    duration = max(0.0, ref_end - ref_start)
                    if duration <= 0.0:
                        continue
                    if has_paired_video_segments:
                        video_segment = video_segments[index - 1]
                        video_start = max(0.0, video_segment.start_s + nudge)
                        video_end = max(video_start, video_segment.end_s + nudge)
                    else:
                        video_start = video_cursor
                        video_end = video_cursor + duration
                    result.append(
                        ReviewSegment(
                            row=row,
                            file_name=file_name,
                            video_path=video_path,
                            reference_path=reference,
                            label=tr(self.language, "review_segment_label", index=index),
                            reference_start_s=ref_start,
                            reference_end_s=ref_end,
                            video_start_s=video_start,
                            video_end_s=video_end,
                            note=note,
                            segment_index=index - 1,
                        )
                    )
                    video_cursor = video_end
                if result:
                    return result

        offset = float(final_item.text())
        video_start = max(0.0, offset)
        reference_start = max(0.0, -offset)
        if abs(offset) >= SUSPICIOUS_GLOBAL_OFFSET_S:
            label = tr(self.language, "review_large_offset_label")
            note = "\n".join(
                (
                    note,
                    tr(
                        self.language,
                        "review_large_offset_note",
                        offset=f"{offset:.2f}",
                        threshold=f"{SUSPICIOUS_GLOBAL_OFFSET_S:.0f}",
                    ),
                )
            )
        else:
            label = tr(self.language, "review_global_label")
        return [
            ReviewSegment(
                row=row,
                file_name=file_name,
                video_path=video_path,
                reference_path=reference,
                label=label,
                reference_start_s=reference_start,
                reference_end_s=reference_start + PREVIEW_DURATION_S,
                video_start_s=video_start,
                video_end_s=video_start + PREVIEW_DURATION_S,
                note=note,
                segment_index=0,
                is_global=True,
            )
        ]

    def _apply_review_adjustments(
        self,
        adjustments_by_row: dict[int, list[ReviewSegmentAdjustment]],
    ) -> None:
        for row, adjustments in adjustments_by_row.items():
            if not adjustments or row >= self.table.rowCount():
                continue
            plan_item = self.table.item(row, 5)
            plan = plan_item.data(Qt.ItemDataRole.UserRole) if plan_item else None
            if isinstance(plan, dict) and plan.get("method") == "segmented":
                updated_plan = self._plan_with_review_adjustments(row, plan, adjustments)
                if plan_item:
                    plan_item.setData(Qt.ItemDataRole.UserRole, updated_plan)
                    plan_item.setToolTip(self._plan_tooltip(updated_plan))
                logger.info(
                    "Applied %d review segment adjustment(s) to row %d",
                    len(adjustments),
                    row,
                )
                continue

            delta_s = adjustments[0].delta_s
            if abs(delta_s) < 0.0005:
                continue
            self._set_row_nudge(row, self._row_nudge(row) + delta_s)
            logger.info("Applied global review nudge %.3f to row %d", delta_s, row)

    def _plan_with_review_adjustments(
        self,
        row: int,
        plan: dict,
        adjustments: list[ReviewSegmentAdjustment],
    ) -> dict:
        nudge = self._row_nudge(row)
        ordered = sorted(adjustments, key=lambda item: item.segment_index)
        video_segments = [
            {
                "start_s": max(0.0, adjustment.video_start_s - nudge),
                "end_s": max(0.0, adjustment.video_end_s - nudge),
            }
            for adjustment in ordered
        ]
        reference_segments = [
            {
                "start_s": adjustment.reference_start_s,
                "end_s": adjustment.reference_end_s,
            }
            for adjustment in ordered
        ]
        duration_s = sum(
            min(
                max(0.0, video["end_s"] - video["start_s"]),
                max(0.0, reference["end_s"] - reference["start_s"]),
            )
            for video, reference in zip(video_segments, reference_segments)
        )
        updated_plan = dict(plan)
        updated_plan["video_segments"] = video_segments
        updated_plan["reference_segments"] = reference_segments
        updated_plan["duration_s"] = duration_s
        updated_plan["review_adjusted"] = True
        if video_segments:
            updated_plan["video_start_s"] = video_segments[0]["start_s"]
            updated_plan["video_end_s"] = video_segments[-1]["end_s"]
        return updated_plan

    def _review_note(self, plan: object, confidence: str) -> str:
        if isinstance(plan, dict):
            warnings = plan.get("warnings")
            if isinstance(warnings, list) and warnings:
                return tr(self.language, "smart_warnings", warnings=", ".join(str(item) for item in warnings))
        return tr(self.language, "review_low_confidence_note", confidence=confidence or "-")

    def _unconfirmed_review_rows(self) -> list[int]:
        rows: list[int] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 7)
            if not item:
                continue
            if bool(item.data(REVIEW_REQUIRED_ROLE)) and item.data(REVIEW_CONFIRMED_ROLE) is not True:
                rows.append(row)
        return rows

    def _mark_review_confirmed(self, row: int) -> None:
        item = self.table.item(row, 7)
        if not item:
            return
        item.setData(REVIEW_CONFIRMED_ROLE, True)
        item.setText(tr(self.language, "review_confirmed"))
        self._set_row_status(row, True)
        logger.info("Review confirmed for row %d", row)

    def _mark_review_dirty(self, row: int) -> None:
        item = self.table.item(row, 7)
        if not item or not bool(item.data(REVIEW_REQUIRED_ROLE)):
            return
        item.setData(REVIEW_CONFIRMED_ROLE, False)
        item.setText(tr(self.language, "review_required"))
        self._set_row_status(row, True, review=True)
        logger.info("Review marked dirty for row %d", row)

    def _row_nudge(self, row: int) -> float:
        spin = self.table.cellWidget(row, 3)
        return spin.value() if isinstance(spin, QDoubleSpinBox) else 0.0

    def _set_row_nudge(self, row: int, value: float) -> None:
        spin = self.table.cellWidget(row, 3)
        if not isinstance(spin, QDoubleSpinBox):
            return
        blocker = QSignalBlocker(spin)
        spin.setValue(value)
        del blocker
        self._update_final_offset(row)

    def _job_plan_values(
        self,
        row: int,
        plan: object,
    ) -> tuple[
        float | None,
        float | None,
        tuple[ReferenceSegment, ...],
        tuple[ReferenceSegment, ...],
    ]:
        if not isinstance(plan, dict) or plan.get("method") != "segmented":
            return None, None, (), ()
        if float(plan.get("trim_total_s") or 0.0) <= 0.0:
            return None, None, (), ()
        reference_segments = self._parse_plan_segments(plan.get("reference_segments"))
        video_segments = self._parse_plan_segments(plan.get("video_segments"))
        if not reference_segments:
            return None, None, (), ()
        nudge = 0.0
        spin = self.table.cellWidget(row, 3)
        if isinstance(spin, QDoubleSpinBox):
            nudge = spin.value()
        if len(video_segments) == len(reference_segments):
            shifted_video_segments = tuple(
                ReferenceSegment(
                    max(0.0, segment.start_s + nudge),
                    max(0.0, segment.end_s + nudge),
                )
                for segment in video_segments
            )
            duration_s = sum(
                min(video_segment.duration_s, reference_segment.duration_s)
                for video_segment, reference_segment in zip(shifted_video_segments, reference_segments)
            )
            video_start_s = shifted_video_segments[0].start_s
            return video_start_s, duration_s, shifted_video_segments, tuple(reference_segments)

        video_start_s = max(0.0, float(plan.get("video_start_s") or 0.0) + nudge)
        duration_s = max(0.0, float(plan.get("duration_s") or 0.0))
        return video_start_s, duration_s, (), tuple(reference_segments)

    def _parse_plan_segments(self, value: object) -> list[ReferenceSegment]:
        if not isinstance(value, list):
            return []
        segments: list[ReferenceSegment] = []
        for raw_segment in value:
            if not isinstance(raw_segment, dict):
                continue
            segment = ReferenceSegment(
                float(raw_segment.get("start_s", 0.0)),
                float(raw_segment.get("end_s", 0.0)),
            )
            if segment.duration_s > 0.0:
                segments.append(segment)
        return segments

    def _plan_tooltip(self, plan: object) -> str:
        if not isinstance(plan, dict):
            return ""
        warnings = plan.get("warnings")
        if not isinstance(warnings, list) or not warnings:
            return ""
        return tr(self.language, "smart_warnings", warnings=", ".join(str(item) for item in warnings))

    def _append_log(self, message: str, level: int = logging.INFO) -> None:
        logger.log(level, message)
        self.log.append(message)

    def _warn(self, message: str) -> None:
        logger.warning(message)
        QMessageBox.warning(self, tr(self.language, "warning_title"), message)
