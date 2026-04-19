"""
job_list_widget.py
------------------
Queue of VideoJob rows. Each row has an inline compression target control
(% or MB) that pre-fills from the global default but can be overridden.
"""

import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QScrollArea, QFrame,
    QSizePolicy, QLineEdit, QComboBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QRegularExpression
from PyQt6.QtGui import QRegularExpressionValidator
from core.video_job import VideoJob, JobStatus, SizeMode


STATUS_STYLE = {
    JobStatus.PENDING:   ("Pending",    "color: #404040;"),
    JobStatus.RUNNING:   ("Processing", "color: #4A9FFF;"),
    JobStatus.DONE:      ("Done",       "color: #3dd68c;"),
    JobStatus.FAILED:    ("Failed",     "color: #f87171;"),
    JobStatus.CANCELLED: ("Cancelled",  "color: #fbbf24;"),
}

PROGRESS_CHUNK_RUNNING   = "QProgressBar::chunk { background-color: #4A9FFF; border-radius: 0; }"
PROGRESS_CHUNK_DONE      = "QProgressBar::chunk { background-color: #3dd68c; border-radius: 0; }"
PROGRESS_CHUNK_FAILED    = "QProgressBar::chunk { background-color: #f87171; border-radius: 0; }"
PROGRESS_CHUNK_CANCELLED = "QProgressBar::chunk { background-color: #fbbf24; border-radius: 0; }"


class JobRowWidget(QWidget):
    remove_requested = pyqtSignal(object)

    def __init__(self, job: VideoJob,
                 default_mode: SizeMode = SizeMode.PERCENT,
                 default_value: float = 50.0,
                 parent=None):
        super().__init__(parent)
        self.job = job
        self.setObjectName("jobRow")
        self.setFixedHeight(66)
        self._build_ui(default_mode, default_value)
        self._sync_to_job()

    def _build_ui(self, default_mode: SizeMode, default_value: float):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Content row
        content = QWidget()
        top = QHBoxLayout(content)
        top.setContentsMargins(16, 10, 14, 10)
        top.setSpacing(10)

        # --- Left: filename + metadata ---
        info = QVBoxLayout()
        info.setSpacing(3)

        self._name_label = QLabel(self.job.display_name())
        self._name_label.setObjectName("jobName")
        self._name_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        self._meta_label = QLabel(self._build_meta())
        self._meta_label.setObjectName("jobMeta")

        info.addWidget(self._name_label)
        info.addWidget(self._meta_label)
        top.addLayout(info)

        # --- Right: size control + status + remove ---
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("%",  SizeMode.PERCENT)
        self._mode_combo.addItem("MB", SizeMode.MB)
        self._mode_combo.setFixedWidth(54)
        self._mode_combo.setFixedHeight(26)
        self._mode_combo.setCurrentIndex(0 if default_mode == SizeMode.PERCENT else 1)
        self._mode_combo.setToolTip("% = reduce by percentage  |  MB = target file size")
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        self._value_input = QLineEdit()
        self._value_input.setObjectName("jobValueInput")
        self._value_input.setFixedWidth(72)
        self._value_input.setFixedHeight(26)
        self._value_input.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._value_input.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"^\d{0,6}(\.\d{0,1})?$"))
        )
        self._value_input.textChanged.connect(self._sync_to_job)
        self._apply_mode_range(default_mode, default_value)

        self._status_label = QLabel("Pending")
        self._status_label.setObjectName("jobStatus")
        self._status_label.setFixedWidth(72)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._status_label.setStyleSheet("color: #404040; font-size: 11px; font-weight: 600;")

        self._remove_btn = QPushButton("✕")
        self._remove_btn.setObjectName("removeButton")
        self._remove_btn.setFixedSize(22, 22)
        self._remove_btn.setToolTip("Remove from queue")
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self.job))

        top.addWidget(self._mode_combo)
        top.addWidget(self._value_input)
        top.addWidget(self._status_label)
        top.addWidget(self._remove_btn)

        outer.addWidget(content)

        # Full-width progress bar at the bottom edge
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(2)
        self._progress_bar.setStyleSheet(
            "QProgressBar { background: #1e1e1e; border: none; border-radius: 0; }"
            + PROGRESS_CHUNK_RUNNING
        )
        outer.addWidget(self._progress_bar)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_meta(self) -> str:
        m = self.job.source_metadata
        if not m:
            return ""
        fps = f"{m.fps:.3f}".rstrip("0").rstrip(".")
        mb  = m.file_size / (1024 * 1024) if m.file_size else 0
        return f"{m.width}×{m.height}  {fps} fps  {m.codec_name.upper()}  {mb:.1f} MB"

    def _apply_mode_range(self, mode: SizeMode, value: float):
        self._value_input.blockSignals(True)
        if mode == SizeMode.PERCENT:
            self._value_input.setPlaceholderText("1–99")
            self._value_input.setToolTip(
                "Reduce file size by this percentage.\n"
                "e.g. 50 → output is half the source size."
            )
        else:
            self._value_input.setPlaceholderText("MB")
            self._value_input.setToolTip("Target output file size in megabytes.")
        self._value_input.setText(str(int(value)) if value == int(value) else f"{value:.1f}")
        self._value_input.blockSignals(False)

    def _current_mode(self) -> SizeMode:
        return self._mode_combo.currentData()

    def _on_mode_changed(self):
        mode = self._current_mode()
        self._apply_mode_range(mode, 50.0 if mode == SizeMode.PERCENT else 15.0)
        self._sync_to_job()

    def _sync_to_job(self):
        self.job.size_mode = self._current_mode()
        try:
            self.job.size_value = float(self._value_input.text())
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Called by JobListWidget
    # ------------------------------------------------------------------

    def set_progress(self, pct: float):
        self._progress_bar.setValue(int(pct))

    def set_status(self, status: JobStatus):
        text, style = STATUS_STYLE.get(status, ("Unknown", ""))
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"{style} font-size: 11px; font-weight: 600;")

        bar_base = "QProgressBar { background: #1e1e1e; border: none; border-radius: 0; } "
        if status == JobStatus.DONE:
            self._progress_bar.setValue(100)
            self._progress_bar.setStyleSheet(bar_base + PROGRESS_CHUNK_DONE)
        elif status == JobStatus.FAILED:
            self._progress_bar.setStyleSheet(bar_base + PROGRESS_CHUNK_FAILED)
        elif status == JobStatus.CANCELLED:
            self._progress_bar.setStyleSheet(bar_base + PROGRESS_CHUNK_CANCELLED)
        elif status == JobStatus.RUNNING:
            self._progress_bar.setStyleSheet(bar_base + PROGRESS_CHUNK_RUNNING)

        is_running = (status == JobStatus.RUNNING)
        self._mode_combo.setEnabled(not is_running)
        self._value_input.setEnabled(not is_running)
        self._remove_btn.setEnabled(not is_running)


class JobListWidget(QWidget):
    job_remove_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: dict[str, JobRowWidget] = {}
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header
        header = QWidget()
        header.setObjectName("queueHeader")
        header.setFixedHeight(28)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 0, 14, 0)

        queue_lbl = QLabel("QUEUE")
        queue_lbl.setObjectName("queueHeaderLabel")

        self._count_label = QLabel("0 files")
        self._count_label.setObjectName("queueCountLabel")
        self._count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        h_layout.addWidget(queue_lbl)
        h_layout.addStretch()
        h_layout.addWidget(self._count_label)
        outer.addWidget(header)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._list_layout = QVBoxLayout(self._container)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._list_layout.setSpacing(0)
        self._list_layout.setContentsMargins(0, 0, 0, 0)

        self._empty_label = QLabel("No files added yet.\nDrop videos above or click to browse.")
        self._empty_label.setObjectName("metaLabel")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #303030; padding: 32px; font-size: 12px;")
        self._list_layout.addWidget(self._empty_label)

        scroll.setWidget(self._container)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_job(self, job: VideoJob,
                default_mode: SizeMode = SizeMode.PERCENT,
                default_value: float = 50.0):
        self._empty_label.setVisible(False)
        row = JobRowWidget(job, default_mode=default_mode, default_value=default_value)
        row.remove_requested.connect(self.job_remove_requested)
        self._rows[job.input_path] = row
        self._list_layout.addWidget(row)
        self._refresh_count()

    def update_progress(self, job: VideoJob, pct: float):
        if row := self._rows.get(job.input_path):
            row.set_progress(pct)

    def update_status(self, job: VideoJob):
        if row := self._rows.get(job.input_path):
            row.set_status(job.status)

    def remove_job(self, job: VideoJob):
        if row := self._rows.pop(job.input_path, None):
            self._list_layout.removeWidget(row)
            row.deleteLater()
        self._refresh_count()

    def _refresh_count(self):
        n = len(self._rows)
        self._count_label.setText(f"{n} file{'s' if n != 1 else ''}")
        self._empty_label.setVisible(n == 0)
