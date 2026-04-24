"""
upscale_panel.py
----------------
UI panel for video upscaling settings.
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.upscaling import REALESRGAN_MODELS, UPSCALE_PRESETS, UpscalingEngine
from core.video_job import UpscaleMode, VideoJob
from ui.widgets import ConsistentComboBox, apply_surface_shadow


class UpscalePanel(QWidget):
    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._engine = UpscalingEngine()
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 6, 4, 10)
        root.setSpacing(0)

        box = QGroupBox("Upscaling")
        apply_surface_shadow(box, blur=24.0, offset_y=5.0)
        root.addWidget(box)

        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 18, 14, 14)

        self._enable_check = QCheckBox("Enable Upscaling")
        self._enable_check.stateChanged.connect(self._on_toggle)
        layout.addWidget(self._enable_check)

        self._options_widget = QWidget()
        self._options_widget.setEnabled(False)
        form = QFormLayout(self._options_widget)

        self._method_combo = ConsistentComboBox()
        self._method_combo.addItem("Lanczos (FFmpeg built-in)", UpscaleMode.LANCZOS)
        self._method_combo.addItem("Real-ESRGAN (AI)", UpscaleMode.REAL_ESRGAN)
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)
        form.addRow("Method:", self._method_combo)

        self._preset_combo = ConsistentComboBox()
        self._preset_combo.addItem("Custom", None)
        for name in UPSCALE_PRESETS:
            self._preset_combo.addItem(name, name)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        form.addRow("Target Resolution:", self._preset_combo)

        self._custom_w = QSpinBox()
        self._custom_w.setRange(1, 7680)
        self._custom_w.setValue(1920)
        self._custom_h = QSpinBox()
        self._custom_h.setRange(1, 4320)
        self._custom_h.setValue(1080)
        custom_row = QWidget()
        cr_layout = QHBoxLayout(custom_row)
        cr_layout.setContentsMargins(0, 0, 0, 0)
        cr_layout.addWidget(self._custom_w)
        cr_layout.addWidget(QLabel("x"))
        cr_layout.addWidget(self._custom_h)
        self._custom_widget = custom_row
        form.addRow("Custom (W x H):", self._custom_widget)

        self._realesrgan_model_combo = ConsistentComboBox()
        self._realesrgan_model_combo.addItems(REALESRGAN_MODELS)
        self._realesrgan_model_combo.currentIndexChanged.connect(self.settings_changed)
        form.addRow("AI Model:", self._realesrgan_model_combo)
        self._realesrgan_model_label = form.labelForField(self._realesrgan_model_combo)

        self._scale_combo = ConsistentComboBox()
        self._scale_combo.addItem("2x", 2)
        self._scale_combo.addItem("4x", 4)
        self._scale_combo.currentIndexChanged.connect(self.settings_changed)
        form.addRow("AI Scale:", self._scale_combo)
        self._scale_label = form.labelForField(self._scale_combo)

        self._info_label = QLabel("")
        self._info_label.setWordWrap(True)
        self._info_label.setObjectName("infoLabel")
        form.addRow(self._info_label)

        layout.addWidget(self._options_widget)
        layout.addStretch()

        self._update_visibility()
        self._on_method_changed()

    def _on_toggle(self, state: int):
        self._options_widget.setEnabled(bool(state))
        self.settings_changed.emit()

    def _on_method_changed(self):
        self._update_visibility()
        if self._method_combo.currentData() == UpscaleMode.REAL_ESRGAN:
            if UpscalingEngine.is_realesrgan_available():
                self._info_label.setText(
                    "Real-ESRGAN gives much better visual detail than plain FFmpeg scaling when the external binary is installed."
                )
            else:
                self._info_label.setText(
                    "Real-ESRGAN is not installed. Install 'realesrgan-ncnn-vulkan' and add it to your PATH to use AI upscaling."
                )
        else:
            self._info_label.setText(
                "Lanczos is the built-in fallback. It is clean and fast, but not as strong as AI upscaling."
            )
        self.settings_changed.emit()

    def _on_preset_changed(self):
        self._update_visibility()
        self.settings_changed.emit()

    def _update_visibility(self):
        is_custom = self._preset_combo.currentData() is None
        is_ai = self._method_combo.currentData() == UpscaleMode.REAL_ESRGAN
        self._custom_widget.setVisible(is_custom)
        self._realesrgan_model_combo.setVisible(is_ai)
        self._scale_combo.setVisible(is_ai)
        if self._realesrgan_model_label is not None:
            self._realesrgan_model_label.setVisible(is_ai)
        if self._scale_label is not None:
            self._scale_label.setVisible(is_ai)

    def apply_to_job(self, job: VideoJob):
        if not self._enable_check.isChecked():
            self._engine.disable(job)
            return

        preset = self._preset_combo.currentData()
        method = self._method_combo.currentData()
        if preset:
            self._engine.apply_preset(job, preset, mode=method)
            return

        if method == UpscaleMode.REAL_ESRGAN:
            self._engine.apply_realesrgan(
                job,
                self._custom_w.value(),
                self._custom_h.value(),
                scale=self._scale_combo.currentData(),
                model_name=self._realesrgan_model_combo.currentText(),
            )
        else:
            self._engine.apply_lanczos(job, self._custom_w.value(), self._custom_h.value())

    def is_enabled(self) -> bool:
        return self._enable_check.isChecked()

    def populate_from_job(self, job: VideoJob):
        active = job.upscale_mode != UpscaleMode.NONE
        self._enable_check.setChecked(active)
        self._options_widget.setEnabled(active)
        if job.upscale_width:
            self._custom_w.setValue(job.upscale_width)
        if job.upscale_height:
            self._custom_h.setValue(job.upscale_height)
        if job.upscale_mode == UpscaleMode.REAL_ESRGAN:
            idx = self._method_combo.findData(UpscaleMode.REAL_ESRGAN)
            if idx >= 0:
                self._method_combo.setCurrentIndex(idx)
            model_idx = self._realesrgan_model_combo.findText(job.upscale_model)
            if model_idx >= 0:
                self._realesrgan_model_combo.setCurrentIndex(model_idx)
            scale_idx = self._scale_combo.findData(job.upscale_scale)
            if scale_idx >= 0:
                self._scale_combo.setCurrentIndex(scale_idx)
