from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
from PyQt6.QtCore import QRectF, QSignalBlocker, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rhythmflow.config import SR
from rhythmflow.core.audio_io import decode_mono_window
from rhythmflow.ui.i18n import tr


logger = logging.getLogger(__name__)


SEGMENT_ROLE = int(Qt.ItemDataRole.UserRole)
BASE_WAVEFORM_ADJUST_LIMIT_S = 2.0
WAVEFORM_ADJUST_PADDING_S = 2.0
MAX_WAVEFORM_ADJUST_LIMIT_S = 120.0
MIN_WAVEFORM_DURATION_S = 0.1


@dataclass(frozen=True)
class ReviewSegment:
    row: int
    file_name: str
    video_path: str
    reference_path: str
    label: str
    reference_start_s: float
    reference_end_s: float
    video_start_s: float
    video_end_s: float
    note: str = ""
    segment_index: int = 0
    is_global: bool = False


@dataclass(frozen=True)
class ReviewSegmentAdjustment:
    row: int
    segment_index: int
    reference_start_s: float
    reference_end_s: float
    video_start_s: float
    video_end_s: float
    delta_s: float


class WaveformAlignmentWidget(QWidget):
    adjustmentChanged = pyqtSignal(float)

    def __init__(self, *, language: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.language = language
        self._segment: ReviewSegment | None = None
        self._reference_samples = np.zeros(0, dtype=np.float32)
        self._video_samples = np.zeros(0, dtype=np.float32)
        self._reference_window_start_s = 0.0
        self._video_window_start_s = 0.0
        self._duration_s = MIN_WAVEFORM_DURATION_S
        self._base_reference_start_s = 0.0
        self._base_video_start_s = 0.0
        self._adjustment_limit_s = BASE_WAVEFORM_ADJUST_LIMIT_S
        self._adjustment_s = 0.0
        self._error_message = ""
        self._drag_start_x: float | None = None
        self._drag_start_adjustment = 0.0
        self.setMinimumHeight(190)
        self.setMouseTracking(True)
        self.setToolTip(tr(self.language, "review_waveform_tooltip"))

    @property
    def adjustment_s(self) -> float:
        return self._adjustment_s

    @property
    def adjustment_bounds(self) -> tuple[float, float]:
        lower = max(-self._adjustment_limit_s, -self._base_video_start_s)
        upper = self._adjustment_limit_s
        return lower, upper

    def set_segment(self, segment: ReviewSegment, adjustment_s: float) -> None:
        self._segment = segment
        self._duration_s = max(
            MIN_WAVEFORM_DURATION_S,
            min(
                max(0.0, segment.reference_end_s - segment.reference_start_s),
                max(0.0, segment.video_end_s - segment.video_start_s),
            ),
        )
        self._base_reference_start_s = max(0.0, segment.reference_start_s)
        self._base_video_start_s = max(0.0, segment.video_start_s)
        self._adjustment_limit_s = _adjustment_limit_for_segment(segment)
        self.set_adjustment(adjustment_s, emit=False)
        self.update()

    def set_waveforms(
        self,
        reference_samples: np.ndarray,
        video_samples: np.ndarray,
        *,
        reference_window_start_s: float,
        video_window_start_s: float,
    ) -> None:
        self._reference_samples = _clean_samples(reference_samples)
        self._video_samples = _clean_samples(video_samples)
        self._reference_window_start_s = max(0.0, reference_window_start_s)
        self._video_window_start_s = max(0.0, video_window_start_s)
        self._error_message = ""
        self.update()

    def set_error(self, message: str) -> None:
        self._reference_samples = np.zeros(0, dtype=np.float32)
        self._video_samples = np.zeros(0, dtype=np.float32)
        self._error_message = message
        self.update()

    def set_adjustment(self, adjustment_s: float, *, emit: bool = True) -> None:
        value = self._clamp_adjustment(adjustment_s)
        if abs(value - self._adjustment_s) < 0.0005:
            return
        self._adjustment_s = value
        self.update()
        if emit:
            self.adjustmentChanged.emit(value)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._video_rect().contains(event.position()):
            self._drag_start_x = float(event.position().x())
            self._drag_start_adjustment = self._adjustment_s
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_start_x is not None:
            rect = self._plot_rect()
            seconds_per_pixel = self._duration_s / max(1.0, rect.width())
            dx = float(event.position().x()) - self._drag_start_x
            self.set_adjustment(
                self._drag_start_adjustment - dx * seconds_per_pixel,
                emit=True,
            )
            event.accept()
            return
        if self._video_rect().contains(event.position()):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start_x is not None:
            self._drag_start_x = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("#0f172a"))

        ref_rect = self._reference_rect()
        video_rect = self._video_rect()
        self._draw_track_background(painter, ref_rect, tr(self.language, "review_wave_reference"))
        self._draw_track_background(painter, video_rect, tr(self.language, "review_wave_video"))
        self._draw_time_ticks(painter)

        if self._error_message:
            painter.setPen(QColor("#fca5a5"))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                self._error_message,
            )
            return

        if self._reference_samples.size:
            self._draw_reference_waveform(painter, ref_rect)
        if self._video_samples.size:
            self._draw_video_waveform(painter, video_rect)
        if not self._reference_samples.size and not self._video_samples.size:
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                tr(self.language, "review_waveform_empty"),
            )

        self._draw_adjustment_label(painter)

    def _draw_track_background(self, painter: QPainter, rect: QRectF, label: str) -> None:
        painter.fillRect(rect, QColor("#111827"))
        painter.setPen(QPen(QColor("#334155"), 1))
        painter.drawRect(rect)
        center_y = int(rect.center().y())
        painter.setPen(QPen(QColor("#475569"), 1))
        painter.drawLine(int(rect.left()), center_y, int(rect.right()), center_y)
        painter.setPen(QColor("#cbd5e1"))
        painter.drawText(
            QRectF(8, rect.top(), 76, rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            label,
        )

    def _draw_reference_waveform(self, painter: QPainter, rect: QRectF) -> None:
        segment = self._segment
        if segment is None:
            return
        window_duration_s = self._reference_samples.size / float(SR)
        if window_duration_s <= 0.0:
            return
        points = max(1, min(5000, int(rect.width() * window_duration_s / self._duration_s)))
        envelope = _peak_envelope(self._reference_samples, points)
        reference_start_s, _, _, _ = _adjusted_spans(segment, self._adjustment_s)
        self._draw_windowed_envelope(
            painter,
            rect,
            envelope,
            QColor("#38bdf8"),
            window_start_s=self._reference_window_start_s,
            window_duration_s=window_duration_s,
            aligned_start_s=reference_start_s,
        )

    def _draw_video_waveform(self, painter: QPainter, rect: QRectF) -> None:
        window_duration_s = self._video_samples.size / float(SR)
        if window_duration_s <= 0.0:
            return
        points = max(1, min(5000, int(rect.width() * window_duration_s / self._duration_s)))
        envelope = _peak_envelope(self._video_samples, points)
        _, _, aligned_start_s, _ = _adjusted_spans(self._segment, self._adjustment_s)
        self._draw_windowed_envelope(
            painter,
            rect,
            envelope,
            QColor("#f472b6"),
            window_start_s=self._video_window_start_s,
            window_duration_s=window_duration_s,
            aligned_start_s=aligned_start_s,
        )

    def _draw_windowed_envelope(
        self,
        painter: QPainter,
        rect: QRectF,
        envelope: np.ndarray,
        color: QColor,
        *,
        window_start_s: float,
        window_duration_s: float,
        aligned_start_s: float,
    ) -> None:
        amplitude = rect.height() * 0.42
        center_y = rect.center().y()
        painter.setPen(QPen(color, 1))
        points = max(1, envelope.size)
        for index, value in enumerate(envelope):
            local_time_s = (index + 0.5) / max(1, points) * window_duration_s
            absolute_time_s = window_start_s + local_time_s
            x = rect.left() + (absolute_time_s - aligned_start_s) / self._duration_s * rect.width()
            if x < rect.left() - 1 or x > rect.right() + 1:
                continue
            half_height = float(value) * amplitude
            painter.drawLine(
                int(round(x)),
                int(round(center_y - half_height)),
                int(round(x)),
                int(round(center_y + half_height)),
            )

    def _draw_envelope(
        self,
        painter: QPainter,
        rect: QRectF,
        envelope: np.ndarray,
        color: QColor,
    ) -> None:
        amplitude = rect.height() * 0.42
        center_y = rect.center().y()
        painter.setPen(QPen(color, 1))
        for index, value in enumerate(envelope):
            x = rect.left() + index
            half_height = float(value) * amplitude
            painter.drawLine(
                int(round(x)),
                int(round(center_y - half_height)),
                int(round(x)),
                int(round(center_y + half_height)),
            )

    def _draw_time_ticks(self, painter: QPainter) -> None:
        rect = self._plot_rect()
        painter.setPen(QPen(QColor("#475569"), 1))
        for ratio in (0.0, 0.5, 1.0):
            x = rect.left() + rect.width() * ratio
            painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))
            label = f"{self._duration_s * ratio:.1f}s"
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(
                QRectF(x - 28, self.height() - 22, 56, 18),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )
            painter.setPen(QPen(QColor("#475569"), 1))

    def _draw_adjustment_label(self, painter: QPainter) -> None:
        text = f"{tr(self.language, 'review_adjustment')}: {self._adjustment_s:+.3f}s"
        painter.setPen(QColor("#f8fafc"))
        painter.drawText(
            QRectF(86, 4, self.width() - 106, 22),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            text,
        )

    def _plot_rect(self) -> QRectF:
        left = 86.0
        right_margin = 18.0
        return QRectF(left, 28.0, max(1.0, self.width() - left - right_margin), max(1.0, self.height() - 56.0))

    def _reference_rect(self) -> QRectF:
        plot = self._plot_rect()
        gap = 20.0
        height = (plot.height() - gap) / 2.0
        return QRectF(plot.left(), plot.top(), plot.width(), height)

    def _video_rect(self) -> QRectF:
        ref_rect = self._reference_rect()
        return QRectF(
            ref_rect.left(),
            ref_rect.bottom() + 20.0,
            ref_rect.width(),
            ref_rect.height(),
        )

    def _clamp_adjustment(self, adjustment_s: float) -> float:
        lower, upper = self.adjustment_bounds
        return min(upper, max(lower, float(adjustment_s)))


class ReviewDialog(QDialog):
    def __init__(
        self,
        segments: Sequence[ReviewSegment],
        *,
        language: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.language = language
        self.segments = list(segments)
        self._stop_at_ms: int | None = None
        self._preview_start_ms = 0
        self._preview_end_ms = 0
        self._active_source: str | None = None
        self._pending_seek_ms: int | None = None
        self._pending_play = False
        self._confirmed_rows: set[int] = set()
        self._segment_deltas: dict[tuple[int, int], float] = {}
        self._adjusted_segments_by_row: dict[int, list[ReviewSegmentAdjustment]] = {}

        self.setWindowTitle(tr(self.language, "review_dialog_title"))
        self.resize(1220, 760)
        self._build_ui()
        self._sync_buttons()
        logger.info("Review dialog initialized with %d segment(s)", len(self.segments))

    @property
    def confirmed_rows(self) -> set[int]:
        return set(self._confirmed_rows)

    @property
    def adjusted_segments_by_row(self) -> dict[int, list[ReviewSegmentAdjustment]]:
        return {row: list(adjustments) for row, adjustments in self._adjusted_segments_by_row.items()}

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.summary = QLabel(
            tr(self.language, "review_dialog_summary", count=len(self.segments))
        )
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(len(self.segments), 5)
        self.table.setHorizontalHeaderLabels(
            [
                tr(self.language, "review_table_file"),
                tr(self.language, "review_table_segment"),
                tr(self.language, "review_table_reference"),
                tr(self.language, "review_table_video"),
                tr(self.language, "review_table_confirm"),
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 170)
        self.table.setColumnWidth(3, 170)
        self.table.setColumnWidth(4, 100)
        for row, segment in enumerate(self.segments):
            self._set_segment_row(row, segment)
        self.table.itemChanged.connect(lambda _item: self._sync_buttons())
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self.table)

        preview = QWidget()
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(8, 0, 0, 0)
        preview_layout.setSpacing(8)
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(440, 250)
        preview_layout.addWidget(self.video_widget, stretch=1)

        playback_controls = QHBoxLayout()
        self.play_reference_button = QPushButton(tr(self.language, "review_play_reference"))
        self.play_video_button = QPushButton(tr(self.language, "review_play_video"))
        self.play_pause_button = QPushButton(tr(self.language, "review_pause"))
        self.stop_button = QPushButton(tr(self.language, "review_stop"))
        self.play_reference_button.clicked.connect(lambda: self._play_selected("reference"))
        self.play_video_button.clicked.connect(lambda: self._play_selected("video"))
        self.play_pause_button.clicked.connect(self._toggle_pause)
        self.stop_button.clicked.connect(self._stop_preview)
        playback_controls.addWidget(self.play_reference_button)
        playback_controls.addWidget(self.play_video_button)
        playback_controls.addWidget(self.play_pause_button)
        playback_controls.addWidget(self.stop_button)
        preview_layout.addLayout(playback_controls)

        timeline = QHBoxLayout()
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 1)
        self.position_slider.sliderMoved.connect(self._seek_preview_relative)
        self.time_label = QLabel("0.00s / 0.00s")
        self.time_label.setMinimumWidth(120)
        timeline.addWidget(self.position_slider, stretch=1)
        timeline.addWidget(self.time_label)
        preview_layout.addLayout(timeline)

        self.waveform = WaveformAlignmentWidget(language=self.language)
        self.waveform.adjustmentChanged.connect(self._on_waveform_adjustment_changed)
        preview_layout.addWidget(self.waveform)

        alignment_controls = QHBoxLayout()
        self.adjust_label = QLabel(tr(self.language, "review_adjustment"))
        self.adjust_spin = QDoubleSpinBox()
        self.adjust_spin.setRange(-BASE_WAVEFORM_ADJUST_LIMIT_S, BASE_WAVEFORM_ADJUST_LIMIT_S)
        self.adjust_spin.setDecimals(3)
        self.adjust_spin.setSingleStep(0.01)
        self.adjust_spin.setSuffix("s")
        self.adjust_spin.valueChanged.connect(self._on_adjust_spin_changed)
        self.reset_adjust_button = QPushButton(tr(self.language, "review_reset_adjustment"))
        self.reset_adjust_button.clicked.connect(lambda: self._set_selected_adjustment(0.0))
        self.confirm_all_button = QPushButton(tr(self.language, "review_confirm_all"))
        self.confirm_all_button.clicked.connect(self._confirm_all)
        alignment_controls.addWidget(self.adjust_label)
        alignment_controls.addWidget(self.adjust_spin)
        alignment_controls.addWidget(self.reset_adjust_button)
        alignment_controls.addStretch(1)
        alignment_controls.addWidget(self.confirm_all_button)
        preview_layout.addLayout(alignment_controls)

        splitter.addWidget(preview)
        splitter.setSizes([600, 620])
        root.addWidget(splitter, stretch=1)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText(
            tr(self.language, "review_accept")
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Cancel).setText(
            tr(self.language, "review_cancel")
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        root.addWidget(self.button_box)

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        if self.segments:
            self.table.selectRow(0)

    def _set_segment_row(self, row: int, segment: ReviewSegment) -> None:
        values = [
            segment.file_name,
            segment.label,
            _format_span(segment.reference_start_s, segment.reference_end_s),
            _format_span(segment.video_start_s, segment.video_end_s),
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(segment.note)
            item.setData(SEGMENT_ROLE, segment)
            self.table.setItem(row, column, item)

        confirm_item = QTableWidgetItem("")
        confirm_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        confirm_item.setCheckState(Qt.CheckState.Unchecked)
        confirm_item.setData(SEGMENT_ROLE, segment)
        confirm_item.setToolTip(segment.note)
        self.table.setItem(row, 4, confirm_item)

    def _selected_segment(self) -> ReviewSegment | None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return None
        item = self.table.item(selected[0].row(), 0)
        value = item.data(SEGMENT_ROLE) if item else None
        return value if isinstance(value, ReviewSegment) else None

    def _on_selection_changed(self) -> None:
        self._stop_preview()
        self._load_selected_waveforms()
        self._sync_buttons()

    def _load_selected_waveforms(self) -> None:
        segment = self._selected_segment()
        if segment is None:
            self.waveform.set_error(tr(self.language, "review_select_segment"))
            return
        delta = self._segment_deltas.get(_segment_key(segment), 0.0)
        self.waveform.set_segment(segment, delta)
        lower, upper = self.waveform.adjustment_bounds
        blocker = QSignalBlocker(self.adjust_spin)
        self.adjust_spin.setRange(lower, upper)
        self.adjust_spin.setValue(delta)
        del blocker

        ref_window_start_s, ref_window_end_s, video_window_start_s, video_window_end_s = (
            _waveform_window_bounds(segment, lower, upper)
        )
        reference_window_duration_s = max(
            MIN_WAVEFORM_DURATION_S,
            ref_window_end_s - ref_window_start_s,
        )
        video_window_duration_s = max(
            MIN_WAVEFORM_DURATION_S,
            video_window_end_s - video_window_start_s,
        )
        try:
            reference_audio = decode_mono_window(
                segment.reference_path,
                SR,
                start_s=ref_window_start_s,
                duration_s=reference_window_duration_s,
            )
            video_audio = decode_mono_window(
                segment.video_path,
                SR,
                start_s=video_window_start_s,
                duration_s=video_window_duration_s,
            )
        except Exception as exc:
            logger.warning("Could not load review waveform for %s: %s", segment.file_name, exc)
            self.waveform.set_error(tr(self.language, "review_waveform_error"))
            return
        self.waveform.set_waveforms(
            reference_audio,
            video_audio,
            reference_window_start_s=ref_window_start_s,
            video_window_start_s=video_window_start_s,
        )

    def _play_selected(self, source: str) -> None:
        segment = self._selected_segment()
        if segment is None:
            logger.warning("Review preview requested without a selected segment")
            QMessageBox.information(
                self,
                tr(self.language, "warning_title"),
                tr(self.language, "review_select_segment"),
            )
            return

        if source == "reference":
            path = segment.reference_path
            start_s, end_s, _, _ = self._adjusted_spans(segment)
        else:
            path = segment.video_path
            _, _, start_s, end_s = self._adjusted_spans(segment)

        start_ms = int(max(0.0, start_s) * 1000)
        end_ms = max(start_ms + 1, int(max(start_s, end_s) * 1000))
        logger.info(
            "Playing review preview: source=%s path=%s start=%.3f end=%.3f",
            source,
            path,
            start_s,
            end_s,
        )
        self.player.stop()
        self._active_source = source
        self._preview_start_ms = start_ms
        self._preview_end_ms = end_ms
        self._stop_at_ms = end_ms
        self._pending_seek_ms = start_ms
        self._pending_play = True
        self._set_position_slider(0)
        self._set_time_label(0, end_ms - start_ms)
        self.position_slider.setRange(0, max(1, end_ms - start_ms))
        self.player.setSource(QUrl.fromLocalFile(str(Path(path))))
        self._start_pending_play_if_ready()
        self._sync_buttons()

    def _toggle_pause(self) -> None:
        if self._active_source is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            return
        if self._stop_at_ms is not None and self.player.position() >= self._stop_at_ms:
            self.player.setPosition(self._preview_start_ms)
        self.player.play()

    def _stop_preview(self) -> None:
        logger.debug("Stopping review preview")
        if hasattr(self, "player"):
            self.player.stop()
        self._stop_at_ms = None
        self._pending_seek_ms = None
        self._pending_play = False
        self._active_source = None
        if hasattr(self, "position_slider"):
            self._set_position_slider(0)
        if hasattr(self, "time_label"):
            self._set_time_label(0, max(0, self._preview_end_ms - self._preview_start_ms))
        self._sync_buttons()

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._pending_seek_ms = None
            self._pending_play = False
            logger.error("Review preview media is invalid")
            QMessageBox.warning(
                self,
                tr(self.language, "warning_title"),
                tr(self.language, "review_preview_error"),
            )
            return
        self._start_pending_play_if_ready()

    def _start_pending_play_if_ready(self) -> None:
        if self._pending_seek_ms is None or not self._pending_play:
            return
        if self.player.mediaStatus() not in {
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        }:
            return
        seek_ms = self._pending_seek_ms
        self._pending_seek_ms = None
        self._pending_play = False
        self.player.setPosition(seek_ms)
        QTimer.singleShot(0, lambda: self.player.setPosition(seek_ms))
        QTimer.singleShot(30, self.player.play)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_pause_button.setText(tr(self.language, "review_pause"))
        else:
            self.play_pause_button.setText(tr(self.language, "review_resume"))
        self._sync_buttons()

    def _on_position_changed(self, position_ms: int) -> None:
        if self._stop_at_ms is not None and position_ms >= self._stop_at_ms:
            self.player.pause()
            if position_ms != self._stop_at_ms:
                self.player.setPosition(self._stop_at_ms)
            position_ms = self._stop_at_ms
        relative_ms = max(0, min(position_ms - self._preview_start_ms, self._preview_end_ms - self._preview_start_ms))
        self._set_position_slider(relative_ms)
        self._set_time_label(relative_ms, max(0, self._preview_end_ms - self._preview_start_ms))

    def _seek_preview_relative(self, value_ms: int) -> None:
        if self._active_source is None:
            return
        target_ms = self._preview_start_ms + max(0, int(value_ms))
        self.player.setPosition(min(target_ms, self._preview_end_ms))

    def _set_position_slider(self, value_ms: int) -> None:
        if not hasattr(self, "position_slider"):
            return
        blocker = QSignalBlocker(self.position_slider)
        self.position_slider.setValue(max(0, int(value_ms)))
        del blocker

    def _set_time_label(self, elapsed_ms: int, duration_ms: int) -> None:
        self.time_label.setText(f"{_format_ms(elapsed_ms)} / {_format_ms(duration_ms)}")

    def _on_waveform_adjustment_changed(self, adjustment_s: float) -> None:
        self._set_selected_adjustment(adjustment_s, source="waveform")

    def _on_adjust_spin_changed(self, adjustment_s: float) -> None:
        self._set_selected_adjustment(adjustment_s, source="spin")

    def _set_selected_adjustment(self, adjustment_s: float, *, source: str = "button") -> None:
        segment = self._selected_segment()
        if segment is None:
            return
        key = _segment_key(segment)
        value = self.waveform._clamp_adjustment(adjustment_s)
        self._segment_deltas[key] = value
        if source != "spin":
            blocker = QSignalBlocker(self.adjust_spin)
            self.adjust_spin.setValue(value)
            del blocker
        if source != "waveform":
            self.waveform.set_adjustment(value, emit=False)
        self._refresh_selected_time_cells(segment)
        if self._active_source == "video":
            self._play_selected("video")
        elif self._active_source == "reference":
            self._play_selected("reference")
        self._sync_buttons()

    def _refresh_selected_time_cells(self, segment: ReviewSegment) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return
        ref_start_s, ref_end_s, video_start_s, video_end_s = self._adjusted_spans(segment)
        reference_item = self.table.item(selected[0].row(), 2)
        if reference_item:
            reference_item.setText(_format_span(ref_start_s, ref_end_s))
        video_item = self.table.item(selected[0].row(), 3)
        if video_item:
            video_item.setText(_format_span(video_start_s, video_end_s))

    def _adjusted_spans(self, segment: ReviewSegment) -> tuple[float, float, float, float]:
        delta = self._segment_deltas.get(_segment_key(segment), 0.0)
        return _adjusted_spans(segment, delta)

    def _confirm_all(self) -> None:
        logger.info("Confirming all review segment checkboxes")
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 4)
            if item:
                item.setCheckState(Qt.CheckState.Checked)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        has_selection = bool(self.table.selectionModel().selectedRows()) if self.table.selectionModel() else False
        has_active_preview = self._active_source is not None
        self.play_reference_button.setEnabled(has_selection)
        self.play_video_button.setEnabled(has_selection)
        self.play_pause_button.setEnabled(has_active_preview)
        self.stop_button.setEnabled(has_active_preview)
        self.position_slider.setEnabled(has_active_preview)
        self.adjust_spin.setEnabled(has_selection)
        self.reset_adjust_button.setEnabled(has_selection)
        all_checked = self._all_segments_checked()
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(all_checked)

    def _all_segments_checked(self) -> bool:
        if self.table.rowCount() == 0:
            return False
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 4)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                return False
        return True

    def accept(self) -> None:
        if not self._all_segments_checked():
            logger.warning("Review accept blocked because not all segments were checked")
            QMessageBox.warning(
                self,
                tr(self.language, "warning_title"),
                tr(self.language, "review_incomplete"),
            )
            return
        self._confirmed_rows = {segment.row for segment in self.segments}
        grouped: defaultdict[int, list[ReviewSegmentAdjustment]] = defaultdict(list)
        for segment in self.segments:
            ref_start_s, ref_end_s, video_start_s, video_end_s = self._adjusted_spans(segment)
            grouped[segment.row].append(
                ReviewSegmentAdjustment(
                    row=segment.row,
                    segment_index=segment.segment_index,
                    reference_start_s=ref_start_s,
                    reference_end_s=ref_end_s,
                    video_start_s=video_start_s,
                    video_end_s=video_end_s,
                    delta_s=self._segment_deltas.get(_segment_key(segment), 0.0),
                )
            )
        self._adjusted_segments_by_row = {
            row: sorted(adjustments, key=lambda item: item.segment_index)
            for row, adjustments in grouped.items()
        }
        self._stop_preview()
        logger.info("Review dialog accepted for %d row(s)", len(self._confirmed_rows))
        super().accept()

    def reject(self) -> None:
        self._stop_preview()
        logger.info("Review dialog rejected")
        super().reject()


def _segment_key(segment: ReviewSegment) -> tuple[int, int]:
    return segment.row, segment.segment_index


def _segment_duration(segment: ReviewSegment) -> float:
    candidates = [
        segment.reference_end_s - segment.reference_start_s,
        segment.video_end_s - segment.video_start_s,
    ]
    positives = [item for item in candidates if item > 0.0]
    if not positives:
        return MIN_WAVEFORM_DURATION_S
    return max(MIN_WAVEFORM_DURATION_S, min(positives))


def _segment_offset_s(segment: ReviewSegment) -> float:
    return float(segment.video_start_s) - float(segment.reference_start_s)


def _adjustment_limit_for_segment(segment: ReviewSegment) -> float:
    current_offset = abs(_segment_offset_s(segment))
    limit = max(BASE_WAVEFORM_ADJUST_LIMIT_S, current_offset + WAVEFORM_ADJUST_PADDING_S)
    return min(MAX_WAVEFORM_ADJUST_LIMIT_S, limit)


def _adjusted_spans(
    segment: ReviewSegment | None,
    adjustment_s: float,
) -> tuple[float, float, float, float]:
    if segment is None:
        return 0.0, 0.0, 0.0, 0.0
    duration_s = _segment_duration(segment)
    if segment.is_global:
        offset_s = _segment_offset_s(segment) + adjustment_s
        reference_start_s = max(0.0, -offset_s)
        video_start_s = max(0.0, offset_s)
        return (
            reference_start_s,
            reference_start_s + duration_s,
            video_start_s,
            video_start_s + duration_s,
        )
    video_start_s = max(0.0, segment.video_start_s + adjustment_s)
    video_end_s = max(video_start_s, segment.video_end_s + adjustment_s)
    return (
        segment.reference_start_s,
        segment.reference_end_s,
        video_start_s,
        video_end_s,
    )


def _waveform_window_bounds(
    segment: ReviewSegment,
    lower_adjustment_s: float,
    upper_adjustment_s: float,
) -> tuple[float, float, float, float]:
    candidates = [lower_adjustment_s, 0.0, upper_adjustment_s]
    spans = [_adjusted_spans(segment, adjustment) for adjustment in candidates]
    ref_start = max(0.0, min(span[0] for span in spans))
    ref_end = max(ref_start + MIN_WAVEFORM_DURATION_S, max(span[1] for span in spans))
    video_start = max(0.0, min(span[2] for span in spans))
    video_end = max(video_start + MIN_WAVEFORM_DURATION_S, max(span[3] for span in spans))
    return ref_start, ref_end, video_start, video_end


def _format_span(start_s: float, end_s: float) -> str:
    return f"{start_s:.2f}s - {end_s:.2f}s"


def _format_ms(value_ms: int) -> str:
    return f"{max(0, value_ms) / 1000.0:.2f}s"


def _clean_samples(samples: np.ndarray) -> np.ndarray:
    arr = np.asarray(samples, dtype=np.float32)
    if arr.ndim != 1:
        arr = np.ravel(arr)
    if arr.size == 0:
        return np.zeros(0, dtype=np.float32)
    return np.nan_to_num(arr, copy=False)


def _peak_envelope(samples: np.ndarray, point_count: int) -> np.ndarray:
    arr = _clean_samples(samples)
    if arr.size == 0 or point_count <= 0:
        return np.zeros(max(0, point_count), dtype=np.float32)
    point_count = max(1, int(point_count))
    edges = np.linspace(0, arr.size, point_count + 1, dtype=np.int64)
    envelope = np.zeros(point_count, dtype=np.float32)
    for index in range(point_count):
        start = int(edges[index])
        end = int(edges[index + 1])
        if end <= start:
            end = min(arr.size, start + 1)
        chunk = arr[start:end]
        if chunk.size:
            envelope[index] = float(np.max(np.abs(chunk)))
    peak = float(np.max(envelope)) if envelope.size else 0.0
    if peak > 1e-6:
        envelope /= peak
    return envelope
