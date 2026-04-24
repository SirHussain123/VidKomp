"""
interp_panel.py
---------------
UI panel for frame interpolation settings.
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QFormLayout, QGroupBox, QLabel, QVBoxLayout, QWidget

from core.interpolation import InterpolationEngine, RIFE_MODELS
from core.video_job import InterpolationMode, VideoJob
from ui.widgets import ConsistentComboBox, apply_surface_shadow


class InterpPanel(QWidget):
    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._engine = InterpolationEngine()
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 6, 4, 10)
        root.setSpacing(0)

        box = QGroupBox("Frame Interpolation")
        apply_surface_shadow(box, blur=24.0, offset_y=5.0)
        root.addWidget(box)

        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 18, 14, 14)

        self._enable_check = QCheckBox("Enable Frame Interpolation")
        self._enable_check.stateChanged.connect(self._on_toggle)
        layout.addWidget(self._enable_check)

        self._options_widget = QWidget()
        self._options_widget.setEnabled(False)
        form = QFormLayout(self._options_widget)

        self._mode_combo = ConsistentComboBox()
        self._mode_combo.addItem("FFmpeg Minterpolate 2x", InterpolationMode.MINTERPOLATE_2X)
        self._mode_combo.addItem("RIFE 2x", InterpolationMode.RIFE_2X)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("Method:", self._mode_combo)

        self._rife_model_combo = ConsistentComboBox()
        self._rife_model_combo.addItems(RIFE_MODELS)
        self._rife_model_combo.currentIndexChanged.connect(self.settings_changed)
        form.addRow("RIFE Model:", self._rife_model_combo)
        self._rife_model_label = form.labelForField(self._rife_model_combo)

        self._info_label = QLabel("")
        self._info_label.setWordWrap(True)
        self._info_label.setObjectName("infoLabel")
        form.addRow(self._info_label)

        layout.addWidget(self._options_widget)
        layout.addStretch()

        self._on_mode_changed()

    def _on_toggle(self, state: int):
        self._options_widget.setEnabled(bool(state))
        self.settings_changed.emit()

    def _on_mode_changed(self):
        mode = self._mode_combo.currentData()
        is_rife = mode == InterpolationMode.RIFE_2X
        self._rife_model_combo.setVisible(is_rife)
        if self._rife_model_label is not None:
            self._rife_model_label.setVisible(is_rife)

        if is_rife:
            if InterpolationEngine.is_rife_available():
                self._info_label.setText(
                    "RIFE offers much better frame generation than FFmpeg interpolation when the external binary is installed."
                )
            else:
                self._info_label.setText(
                    "RIFE is not installed. Install 'rife-ncnn-vulkan' and add it to your PATH to use higher-quality frame generation."
                )
        else:
            self._info_label.setText(
                "FFmpeg minterpolate is built in and always available, but it is still lower quality than RIFE."
            )
        self.settings_changed.emit()

    def apply_to_job(self, job: VideoJob):
        if self._enable_check.isChecked():
            mode = self._mode_combo.currentData()
            if mode == InterpolationMode.RIFE_2X:
                self._engine.apply_rife(job, self._rife_model_combo.currentText())
            else:
                self._engine.apply_2x(job)
        else:
            self._engine.disable(job)

    def is_enabled(self) -> bool:
        return self._enable_check.isChecked()

    def populate_from_job(self, job: VideoJob):
        is_active = job.interpolation_mode != InterpolationMode.NONE
        self._enable_check.setChecked(is_active)
        self._options_widget.setEnabled(is_active)
        if job.interpolation_mode == InterpolationMode.RIFE_2X:
            idx = self._mode_combo.findData(InterpolationMode.RIFE_2X)
            if idx >= 0:
                self._mode_combo.setCurrentIndex(idx)
            model_idx = self._rife_model_combo.findText(job.interpolation_model)
            if model_idx >= 0:
                self._rife_model_combo.setCurrentIndex(model_idx)
