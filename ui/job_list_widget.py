"""
job_list_widget.py
------------------
Queue of VideoJob rows with compact workflow summaries and expandable controls.
"""

from PyQt6.QtCore import QElapsedTimer, QRegularExpression, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QRegularExpressionValidator
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.compression import CompressionEngine, CompressionLimits
from core.interpolation import InterpolationEngine, RIFE_MODELS
from core.upscaling import REALESRGAN_MODELS, UPSCALE_PRESETS, UpscalingEngine
from core.video_job import (
    FrameGenOutputPreset,
    InterpolationMode,
    JobStatus,
    SizeMode,
    UpscaleMode,
    VideoJob,
)
from ui.compression_shortcuts import SHORTCUT_PRESETS
from ui.widgets import ConsistentComboBox, NoWheelSpinBox


STATUS_STYLE = {
    JobStatus.PENDING: ("Pending", "color: #8c7b6b;"),
    JobStatus.RUNNING: ("Processing", "color: #cc7a2f;"),
    JobStatus.DONE: ("Done", "color: #4d8b57;"),
    JobStatus.FAILED: ("Failed", "color: #c24f42;"),
    JobStatus.CANCELLED: ("Cancelled", "color: #b7882c;"),
}

PROGRESS_CHUNK_RUNNING = "QProgressBar::chunk { background-color: #cc7a2f; border-radius: 0; }"
PROGRESS_CHUNK_DONE = "QProgressBar::chunk { background-color: #4d8b57; border-radius: 0; }"
PROGRESS_CHUNK_FAILED = "QProgressBar::chunk { background-color: #c24f42; border-radius: 0; }"
PROGRESS_CHUNK_CANCELLED = "QProgressBar::chunk { background-color: #b7882c; border-radius: 0; }"
MAX_NAME_CHARS = 34


class JobRowWidget(QWidget):
    remove_requested = pyqtSignal(object)

    def __init__(
        self,
        job: VideoJob,
        default_mode: SizeMode = SizeMode.PERCENT,
        default_value: float = 50.0,
        parent=None,
    ):
        super().__init__(parent)
        self.job = job
        self._compression_engine = CompressionEngine()
        self._interp_engine = InterpolationEngine()
        self._upscale_engine = UpscalingEngine()
        self._limits = self._build_limits()
        self._details_expanded = False
        self._progress_timer = QElapsedTimer()
        self._latest_progress_pct = 0.0
        self._eta_refresh_timer = QTimer(self)
        self._eta_refresh_timer.setInterval(1000)
        self._eta_refresh_timer.timeout.connect(self._refresh_eta_label)
        self.setObjectName("jobRow")
        self._build_ui(default_mode, default_value)
        self._sync_to_job()
        self._refresh_summary()
        self._refresh_detail_visibility()

    def _build_limits(self) -> CompressionLimits | None:
        meta = self.job.source_metadata
        if not meta:
            return None
        try:
            return self._compression_engine.get_limits(meta)
        except ValueError:
            return None

    def _build_ui(self, default_mode: SizeMode, default_value: float):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 12, 14, 12)
        content_layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)

        info = QVBoxLayout()
        info.setSpacing(4)

        full_name = self.job.display_name()
        truncated_name = self._truncate_name(full_name)
        self._name_label = QLabel(truncated_name)
        self._name_label.setObjectName("jobName")
        self._name_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._name_label.setToolTip(full_name if truncated_name != full_name else "")

        self._meta_label = QLabel(self._build_meta())
        self._meta_label.setObjectName("jobMeta")

        info.addWidget(self._name_label)
        info.addWidget(self._meta_label)
        top.addLayout(info, 1)

        right = QVBoxLayout()
        right.setSpacing(4)

        self._summary_label = QLabel("")
        self._summary_label.setObjectName("jobMeta")
        self._summary_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        actions = QHBoxLayout()
        actions.setSpacing(8)

        self._toggle_btn = QPushButton("Workflow")
        self._toggle_btn.setFixedHeight(28)
        self._toggle_btn.clicked.connect(self._toggle_details)

        self._status_label = QLabel("Pending")
        self._status_label.setObjectName("jobStatus")
        self._status_label.setFixedWidth(84)
        self._status_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._status_label.setStyleSheet(
            "color: #8c7b6b; font-size: 11px; font-weight: 600;"
        )

        self._remove_btn = QPushButton("x")
        self._remove_btn.setObjectName("removeButton")
        self._remove_btn.setFixedSize(24, 24)
        self._remove_btn.setToolTip("Remove from queue")
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self.job))

        actions.addWidget(self._toggle_btn)
        actions.addWidget(self._status_label)
        actions.addWidget(self._remove_btn)

        right.addWidget(self._summary_label)
        right.addLayout(actions)
        top.addLayout(right)

        content_layout.addLayout(top)

        self._eta_label = QLabel("")
        self._eta_label.setObjectName("jobEta")
        self._eta_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        content_layout.addWidget(self._eta_label)

        self._details_widget = QWidget()
        details = QVBoxLayout(self._details_widget)
        details.setContentsMargins(0, 2, 0, 0)
        details.setSpacing(8)

        workflow_row = QHBoxLayout()
        workflow_row.setSpacing(12)

        self._compress_check = QCheckBox("Compress")
        self._compress_check.setChecked(self.job.compress_enabled)
        self._compress_check.stateChanged.connect(self._on_workflow_changed)
        self._upscale_check = QCheckBox("Upscale")
        self._upscale_check.setChecked(self.job.upscale_enabled)
        self._upscale_check.stateChanged.connect(self._on_workflow_changed)
        self._interp_check = QCheckBox("Frame Gen")
        self._interp_check.setChecked(self.job.interpolation_enabled)
        self._interp_check.stateChanged.connect(self._on_workflow_changed)

        workflow_row.addWidget(self._compress_check)
        workflow_row.addWidget(self._upscale_check)
        workflow_row.addWidget(self._interp_check)
        workflow_row.addStretch()
        details.addLayout(workflow_row)

        self._compression_controls = QWidget()
        compression_layout = QHBoxLayout(self._compression_controls)
        compression_layout.setContentsMargins(0, 0, 0, 0)
        compression_layout.setSpacing(8)

        self._shortcut_combo = ConsistentComboBox()
        self._shortcut_combo.addItems(list(SHORTCUT_PRESETS.keys()))
        self._shortcut_combo.setFixedWidth(126)
        self._shortcut_combo.setFixedHeight(30)
        self._shortcut_combo.setToolTip(
            "Quick app target. Choose Custom to edit the size manually."
        )
        self._shortcut_combo.currentIndexChanged.connect(self._on_shortcut_changed)

        self._mode_combo = ConsistentComboBox()
        self._mode_combo.addItem("%", SizeMode.PERCENT)
        self._mode_combo.addItem("MB", SizeMode.MB)
        self._mode_combo.setFixedWidth(58)
        self._mode_combo.setFixedHeight(30)
        self._mode_combo.setCurrentIndex(0 if default_mode == SizeMode.PERCENT else 1)
        self._mode_combo.setToolTip("% = reduce by percentage | MB = target file size")
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        self._value_input = QLineEdit()
        self._value_input.setObjectName("jobValueInput")
        self._value_input.setFixedWidth(76)
        self._value_input.setFixedHeight(30)
        self._value_input.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._value_input.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"^\d{0,6}(\.\d{0,1})?$"))
        )
        self._value_input.textChanged.connect(self._sync_to_job)
        self._apply_mode_range(default_mode, default_value)

        self._compression_hint = QLabel("")
        self._compression_hint.setObjectName("jobMeta")

        compression_layout.addWidget(self._shortcut_combo)
        compression_layout.addWidget(self._mode_combo)
        compression_layout.addWidget(self._value_input)
        compression_layout.addWidget(self._compression_hint, 1)
        details.addWidget(self._compression_controls)

        self._interp_controls = QWidget()
        interp_layout = QHBoxLayout(self._interp_controls)
        interp_layout.setContentsMargins(0, 0, 0, 0)
        interp_layout.setSpacing(8)

        self._interp_mode_combo = ConsistentComboBox()
        self._interp_mode_combo.addItem("FFmpeg 2x", InterpolationMode.MINTERPOLATE_2X)
        self._interp_mode_combo.addItem("RIFE 2x", InterpolationMode.RIFE_2X)
        if InterpolationEngine.is_rife_available():
            self._interp_mode_combo.setCurrentIndex(1)
        self._interp_mode_combo.setFixedWidth(130)
        self._interp_mode_combo.currentIndexChanged.connect(self._on_interp_mode_changed)

        self._interp_model_combo = ConsistentComboBox()
        self._interp_model_combo.addItems(RIFE_MODELS)
        self._interp_model_combo.setFixedWidth(140)
        self._interp_model_combo.currentIndexChanged.connect(self._sync_to_job)

        self._interp_output_combo = ConsistentComboBox()
        self._interp_output_combo.addItem("Smaller", FrameGenOutputPreset.SMALLER)
        self._interp_output_combo.addItem("Balanced", FrameGenOutputPreset.BALANCED)
        self._interp_output_combo.addItem("Higher Quality", FrameGenOutputPreset.HIGHER_QUALITY)
        self._interp_output_combo.setCurrentIndex(1)
        self._interp_output_combo.setFixedWidth(130)
        self._interp_output_combo.currentIndexChanged.connect(self._sync_to_job)

        self._interp_hint = QLabel("")
        self._interp_hint.setObjectName("jobMeta")
        self._interp_hint.setWordWrap(True)

        interp_layout.addWidget(QLabel("Frame Gen:"))
        interp_layout.addWidget(self._interp_mode_combo)
        interp_layout.addWidget(self._interp_model_combo)
        interp_layout.addWidget(self._interp_output_combo)
        interp_layout.addWidget(self._interp_hint, 1)
        details.addWidget(self._interp_controls)

        self._upscale_controls = QWidget()
        upscale_layout = QHBoxLayout(self._upscale_controls)
        upscale_layout.setContentsMargins(0, 0, 0, 0)
        upscale_layout.setSpacing(8)

        self._upscale_method_combo = ConsistentComboBox()
        self._upscale_method_combo.addItem("Lanczos", UpscaleMode.LANCZOS)
        self._upscale_method_combo.addItem("Real-ESRGAN", UpscaleMode.REAL_ESRGAN)
        self._upscale_method_combo.setFixedWidth(132)
        self._upscale_method_combo.currentIndexChanged.connect(self._on_upscale_method_changed)

        self._upscale_preset_combo = ConsistentComboBox()
        self._upscale_preset_combo.addItem("Custom", None)
        self._upscale_preset_combo.addItem("Original", "Original")
        for name in UPSCALE_PRESETS:
            self._upscale_preset_combo.addItem(name, name)
        self._upscale_preset_combo.setFixedWidth(120)
        self._upscale_preset_combo.currentIndexChanged.connect(self._on_upscale_preset_changed)

        self._upscale_w_spin = NoWheelSpinBox()
        self._upscale_w_spin.setRange(2, 7680)
        self._upscale_w_spin.setValue(self.job.target_width or 1920)
        self._upscale_w_spin.setFixedWidth(84)
        self._upscale_w_spin.valueChanged.connect(self._sync_to_job)

        self._upscale_h_spin = NoWheelSpinBox()
        self._upscale_h_spin.setRange(2, 4320)
        self._upscale_h_spin.setValue(self.job.target_height or 1080)
        self._upscale_h_spin.setFixedWidth(84)
        self._upscale_h_spin.valueChanged.connect(self._sync_to_job)

        self._upscale_model_combo = ConsistentComboBox()
        self._upscale_model_combo.addItems(REALESRGAN_MODELS)
        self._upscale_model_combo.setFixedWidth(180)
        self._upscale_model_combo.currentIndexChanged.connect(self._sync_to_job)

        self._upscale_scale_combo = ConsistentComboBox()
        self._upscale_scale_combo.addItem("2x", 2)
        self._upscale_scale_combo.addItem("4x", 4)
        self._upscale_scale_combo.setFixedWidth(72)
        self._upscale_scale_combo.currentIndexChanged.connect(self._sync_to_job)

        self._upscale_hint = QLabel("")
        self._upscale_hint.setObjectName("jobMeta")
        self._upscale_hint.setWordWrap(True)

        upscale_layout.addWidget(QLabel("Upscale:"))
        upscale_layout.addWidget(self._upscale_method_combo)
        upscale_layout.addWidget(self._upscale_preset_combo)
        upscale_layout.addWidget(self._upscale_w_spin)
        upscale_layout.addWidget(QLabel("x"))
        upscale_layout.addWidget(self._upscale_h_spin)
        upscale_layout.addWidget(self._upscale_model_combo)
        upscale_layout.addWidget(self._upscale_scale_combo)
        upscale_layout.addWidget(self._upscale_hint, 1)
        details.addWidget(self._upscale_controls)

        content_layout.addWidget(self._details_widget)
        outer.addWidget(content)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 1000)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(3)
        self._progress_bar.setStyleSheet(
            "QProgressBar { background: #eadfce; border: none; border-radius: 0; }"
            + PROGRESS_CHUNK_RUNNING
        )
        outer.addWidget(self._progress_bar)

    def _truncate_name(self, name: str) -> str:
        if len(name) <= MAX_NAME_CHARS:
            return name
        return f"{name[:MAX_NAME_CHARS - 1]}..."

    def _build_meta(self) -> str:
        meta = self.job.source_metadata
        if not meta:
            return ""
        fps = f"{meta.fps:.3f}".rstrip("0").rstrip(".")
        mb = meta.file_size / (1024 * 1024) if meta.file_size else 0
        return f"{meta.width}x{meta.height}  {fps} fps  {meta.codec_name.upper()}  {mb:.1f} MB"

    def _build_summary(self) -> str:
        parts: list[str] = []
        if self.job.compress_enabled:
            unit = "%" if self.job.size_mode == SizeMode.PERCENT else "MB"
            value = (
                f"{int(self.job.size_value)}"
                if self.job.size_value == int(self.job.size_value)
                else f"{self.job.size_value:.1f}"
            )
            parts.append(f"Compress {value} {unit}")
        if self.job.upscale_enabled:
            parts.append("Upscale")
        if self.job.interpolation_enabled:
            parts.append("Frame Gen")
        if not parts:
            return "No workflow selected"
        return " + ".join(parts)

    def _refresh_summary(self):
        self._summary_label.setText(self._build_summary())

    def _toggle_details(self):
        self._details_expanded = not self._details_expanded
        self._refresh_detail_visibility()

    def _refresh_detail_visibility(self):
        if not hasattr(self, "_details_widget"):
            return
        self._details_widget.setVisible(self._details_expanded)
        self._toggle_btn.setText("Hide" if self._details_expanded else "Workflow")
        if hasattr(self, "_compression_controls"):
            self._compression_controls.setVisible(self._compress_check.isChecked())
        if hasattr(self, "_interp_controls"):
            self._interp_controls.setVisible(self._interp_check.isChecked())
        if hasattr(self, "_upscale_controls"):
            self._upscale_controls.setVisible(self._upscale_check.isChecked())
        self._refresh_interp_hint()
        self._refresh_upscale_visibility()
        self._refresh_compression_hint()

    def _refresh_compression_hint(self):
        if not hasattr(self, "_compression_hint"):
            return
        if not self._compress_check.isChecked():
            self._compression_hint.setText("")
            return
        if self._current_mode() == SizeMode.PERCENT:
            floor = self._limits.max_reduction_pct if self._limits else 99.0
            self._compression_hint.setText(f"Decoder floor: about {floor:.0f}% max reduction")
        else:
            floor = self._limits.min_target_mb if self._limits else 0.1
            self._compression_hint.setText(f"Decoder floor: about {floor:.1f} MB")

    def _refresh_interp_hint(self):
        if not hasattr(self, "_interp_hint"):
            return
        is_rife = self._interp_mode_combo.currentData() == InterpolationMode.RIFE_2X
        self._interp_model_combo.setVisible(is_rife)
        if not self._interp_check.isChecked():
            self._interp_hint.setText("")
            return
        if is_rife:
            if InterpolationEngine.is_rife_available():
                self._interp_hint.setText("Higher quality, heavier GPU load.")
            else:
                self._interp_hint.setText("RIFE binary not found in ai/frame_generation/rife or PATH.")
        else:
            self._interp_hint.setText("Built in, lighter setup, lower quality.")

    def _refresh_upscale_visibility(self):
        if not hasattr(self, "_upscale_controls"):
            return
        is_ai = self._upscale_method_combo.currentData() == UpscaleMode.REAL_ESRGAN
        preset = self._upscale_preset_combo.currentData()
        is_custom = preset is None
        self._upscale_w_spin.setVisible(is_custom)
        self._upscale_h_spin.setVisible(is_custom)
        self._upscale_model_combo.setVisible(is_ai)
        self._upscale_scale_combo.setVisible(is_ai)
        if not self._upscale_check.isChecked():
            self._upscale_hint.setText("")
            return
        if is_ai:
            if UpscalingEngine.is_realesrgan_available():
                self._upscale_hint.setText("AI restore/sharpen or upscale. Very slow on long/high-FPS videos.")
            else:
                self._upscale_hint.setText("Real-ESRGAN binary not found in ai/upscaling/realesrgan or PATH.")
        else:
            self._upscale_hint.setText("Fast built-in resize with lighter system load.")

    def _apply_mode_range(self, mode: SizeMode, value: float):
        self._value_input.blockSignals(True)
        if mode == SizeMode.PERCENT:
            self._value_input.setPlaceholderText("1-99")
            self._value_input.setToolTip("Reduce file size by this percentage.")
        else:
            self._value_input.setPlaceholderText("MB")
            self._value_input.setToolTip("Target output file size in megabytes.")
        self._value_input.setText(
            str(int(value)) if value == int(value) else f"{value:.1f}"
        )
        self._value_input.blockSignals(False)
        self._refresh_compression_hint()

    def _current_mode(self) -> SizeMode:
        return self._mode_combo.currentData()

    def _on_mode_changed(self):
        if self._shortcut_combo.currentText() != "Custom":
            return
        mode = self._current_mode()
        default_value = 50.0 if mode == SizeMode.PERCENT else 15.0
        self._apply_mode_range(mode, default_value)
        self._sync_to_job()

    def _on_shortcut_changed(self):
        preset = SHORTCUT_PRESETS.get(self._shortcut_combo.currentText())
        is_custom = preset is None
        self._mode_combo.setEnabled(is_custom)
        self._value_input.setEnabled(is_custom)

        if preset is not None:
            self._set_mode_value(preset.size_mode, preset.size_value)
            self._shortcut_combo.setToolTip(preset.description)
        else:
            self._shortcut_combo.setToolTip(
                "Quick app target. Choose Custom to edit the size manually."
            )

        self._sync_to_job()

    def _set_mode_value(self, mode: SizeMode, value: float):
        self._mode_combo.blockSignals(True)
        self._mode_combo.setCurrentIndex(0 if mode == SizeMode.PERCENT else 1)
        self._mode_combo.blockSignals(False)
        self._apply_mode_range(mode, value)

    def _on_interp_mode_changed(self):
        is_rife = self._interp_mode_combo.currentData() == InterpolationMode.RIFE_2X
        self._interp_model_combo.setVisible(is_rife)
        self._refresh_interp_hint()
        self._sync_to_job()

    def _on_upscale_method_changed(self):
        self._refresh_upscale_visibility()
        self._sync_to_job()

    def _on_upscale_preset_changed(self):
        preset = self._upscale_preset_combo.currentData()
        if preset == "Original" and self.job.source_metadata:
            self._upscale_w_spin.setValue(self.job.source_metadata.width)
            self._upscale_h_spin.setValue(self.job.source_metadata.height)
        elif preset in UPSCALE_PRESETS:
            width, height = UPSCALE_PRESETS[preset]
            self._upscale_w_spin.setValue(width)
            self._upscale_h_spin.setValue(height)
        self._refresh_upscale_visibility()
        self._sync_to_job()

    def _on_workflow_changed(self):
        self.job.compress_enabled = self._compress_check.isChecked()
        self.job.upscale_enabled = self._upscale_check.isChecked()
        self.job.interpolation_enabled = self._interp_check.isChecked()
        self._refresh_detail_visibility()
        self._sync_to_job()
        self._refresh_summary()

    def _sync_to_job(self):
        self.job.compress_enabled = self._compress_check.isChecked()
        self.job.upscale_enabled = self._upscale_check.isChecked()
        self.job.interpolation_enabled = self._interp_check.isChecked()

        self.job.size_mode = self._current_mode()
        try:
            self.job.size_value = float(self._value_input.text())
        except ValueError:
            pass

        if self.job.interpolation_enabled:
            mode = self._interp_mode_combo.currentData()
            self.job.framegen_output_preset = self._interp_output_combo.currentData()
            if mode == InterpolationMode.RIFE_2X:
                self._interp_engine.apply_rife(
                    self.job,
                    self._interp_model_combo.currentText(),
                )
            else:
                self._interp_engine.apply_2x(self.job)
        else:
            self._interp_engine.disable(self.job)

        if self.job.upscale_enabled:
            preset = self._upscale_preset_combo.currentData()
            if preset == "Original" and self.job.source_metadata:
                width = self.job.source_metadata.width
                height = self.job.source_metadata.height
            else:
                width = self._upscale_w_spin.value()
                height = self._upscale_h_spin.value()
            method = self._upscale_method_combo.currentData()
            if method == UpscaleMode.REAL_ESRGAN:
                self._upscale_engine.apply_realesrgan(
                    self.job,
                    width,
                    height,
                    scale=self._upscale_scale_combo.currentData(),
                    model_name=self._upscale_model_combo.currentText(),
                )
            else:
                self._upscale_engine.apply_lanczos(self.job, width, height)
        else:
            self._upscale_engine.disable(self.job)

        self._refresh_interp_hint()
        self._refresh_upscale_visibility()
        self._refresh_summary()

    def set_progress(self, pct: float):
        pct = max(0.0, min(100.0, pct))
        self._latest_progress_pct = pct
        self._progress_bar.setValue(round(pct * 10))
        if self.job.status == JobStatus.RUNNING:
            self._refresh_eta_label()

    def set_status(self, status: JobStatus):
        self.job.status = status
        text, style = STATUS_STYLE.get(status, ("Unknown", ""))
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"{style} font-size: 11px; font-weight: 600;")

        bar_base = (
            "QProgressBar { background: #eadfce; border: none; border-radius: 0; } "
        )
        if status == JobStatus.DONE:
            self._eta_refresh_timer.stop()
            self._progress_bar.setValue(1000)
            self._progress_bar.setStyleSheet(bar_base + PROGRESS_CHUNK_DONE)
            self._eta_label.setText("Complete")
        elif status == JobStatus.FAILED:
            self._eta_refresh_timer.stop()
            self._progress_bar.setStyleSheet(bar_base + PROGRESS_CHUNK_FAILED)
            self._eta_label.setText("Failed")
        elif status == JobStatus.CANCELLED:
            self._eta_refresh_timer.stop()
            self._progress_bar.setStyleSheet(bar_base + PROGRESS_CHUNK_CANCELLED)
            self._eta_label.setText("Cancelled")
        elif status == JobStatus.RUNNING:
            self._progress_bar.setStyleSheet(bar_base + PROGRESS_CHUNK_RUNNING)
            self._progress_timer.restart()
            self._latest_progress_pct = max(self._latest_progress_pct, self.job.progress)
            self._eta_refresh_timer.start()
            self._eta_label.setText("Estimating remaining time...")
        else:
            self._eta_refresh_timer.stop()
            self._eta_label.setText("")

        is_running = status == JobStatus.RUNNING
        self._toggle_btn.setEnabled(not is_running)
        self._shortcut_combo.setEnabled(not is_running and self._shortcut_combo.currentText() == "Custom")
        if self._shortcut_combo.currentText() != "Custom":
            self._shortcut_combo.setEnabled(not is_running)
        self._mode_combo.setEnabled(not is_running and self._compress_check.isChecked() and self._shortcut_combo.currentText() == "Custom")
        self._value_input.setEnabled(not is_running and self._compress_check.isChecked() and self._shortcut_combo.currentText() == "Custom")
        self._interp_mode_combo.setEnabled(not is_running and self._interp_check.isChecked())
        self._interp_model_combo.setEnabled(not is_running and self._interp_check.isChecked())
        self._interp_output_combo.setEnabled(not is_running and self._interp_check.isChecked())
        self._upscale_method_combo.setEnabled(not is_running and self._upscale_check.isChecked())
        self._upscale_preset_combo.setEnabled(not is_running and self._upscale_check.isChecked())
        self._upscale_w_spin.setEnabled(not is_running and self._upscale_check.isChecked())
        self._upscale_h_spin.setEnabled(not is_running and self._upscale_check.isChecked())
        self._upscale_model_combo.setEnabled(not is_running and self._upscale_check.isChecked())
        self._upscale_scale_combo.setEnabled(not is_running and self._upscale_check.isChecked())
        self._compress_check.setEnabled(not is_running)
        self._upscale_check.setEnabled(not is_running)
        self._interp_check.setEnabled(not is_running)
        self._remove_btn.setEnabled(not is_running)

    def _build_eta_text(self, pct: float) -> str:
        if pct <= 0.5 or not self._progress_timer.isValid():
            elapsed_s = self._progress_timer.elapsed() / 1000.0 if self._progress_timer.isValid() else 0.0
            return f"{pct:.1f}% - elapsed {self._format_duration(elapsed_s)} - estimating remaining time..."

        elapsed_s = self._progress_timer.elapsed() / 1000.0
        remaining_s = max(0.0, elapsed_s * (100.0 - pct) / pct)
        return (
            f"{pct:.1f}% - elapsed {self._format_duration(elapsed_s)}"
            f" - about {self._format_duration(remaining_s)} remaining"
        )

    def _refresh_eta_label(self):
        if self.job.status == JobStatus.RUNNING:
            self._eta_label.setText(self._build_eta_text(self._latest_progress_pct))

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total = max(0, int(round(seconds)))
        hours, rem = divmod(total, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            return f"{hours}h {minutes:02d}m"
        if minutes:
            return f"{minutes}m {secs:02d}s"
        return f"{secs}s"


class JobListWidget(QWidget):
    job_remove_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: dict[int, JobRowWidget] = {}
        self._build_ui()

    def _job_key(self, job: VideoJob) -> int:
        return id(job)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        header.setObjectName("queueHeader")
        header.setFixedHeight(34)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 0, 14, 0)

        queue_lbl = QLabel("QUEUE")
        queue_lbl.setObjectName("queueHeaderLabel")

        self._count_label = QLabel("0 files")
        self._count_label.setObjectName("queueCountLabel")
        self._count_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        h_layout.addWidget(queue_lbl)
        h_layout.addStretch()
        h_layout.addWidget(self._count_label)
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._list_layout = QVBoxLayout(self._container)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._list_layout.setSpacing(8)
        self._list_layout.setContentsMargins(0, 0, 0, 0)

        self._empty_label = QLabel(
            "No files added yet.\nAdd a source above to start building your queue."
        )
        self._empty_label.setObjectName("metaLabel")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            "color: #bfbeb3; padding: 42px; font-size: 12px;"
        )
        self._list_layout.addWidget(self._empty_label)

        scroll.setWidget(self._container)
        outer.addWidget(scroll)

    def add_job(
        self,
        job: VideoJob,
        default_mode: SizeMode = SizeMode.PERCENT,
        default_value: float = 50.0,
    ):
        self._empty_label.setVisible(False)
        row = JobRowWidget(job, default_mode=default_mode, default_value=default_value)
        row.remove_requested.connect(self.job_remove_requested)
        self._rows[self._job_key(job)] = row
        self._list_layout.addWidget(row)
        self._refresh_count()

    def update_progress(self, job: VideoJob, pct: float):
        if row := self._rows.get(self._job_key(job)):
            row.set_progress(pct)

    def update_status(self, job: VideoJob):
        if row := self._rows.get(self._job_key(job)):
            row.set_status(job.status)

    def remove_job(self, job: VideoJob):
        if row := self._rows.pop(self._job_key(job), None):
            self._list_layout.removeWidget(row)
            row.hide()
            row.setParent(None)
            row.deleteLater()
            self._list_layout.invalidate()
            self._container.updateGeometry()
        self._refresh_count()

    def _refresh_count(self):
        count = len(self._rows)
        self._count_label.setText(f"{count} file{'s' if count != 1 else ''}")
        self._empty_label.setVisible(count == 0)
