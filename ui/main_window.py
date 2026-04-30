"""
main_window.py
--------------
Main application shell with sidebar navigation and processing pages.
"""

import os

from PyQt6.QtCore import QPointF, QTimer
from PyQt6.QtGui import QCursor, QEnterEvent
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from core.job_queue import JobQueue
from core.compression import CompressionEngine
from core.video_job import InterpolationMode, JobStatus, SizeMode, UpscaleMode, VideoJob
from core.video_probe import VideoProbe
from ui.advanced_settings import AdvancedSettingsPanel
from ui.basic_settings import BasicSettingsPanel
from ui.compare_page import ComparePage
from ui.file_drop_widget import FileDropWidget
from ui.job_list_widget import JobListWidget
from ui.system_panel import SystemPanel
from ui.widgets import NavButton, apply_surface_shadow
from utils.file_utils import FileUtils


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._queue = JobQueue(parent=self)
        self._queue.job_started.connect(self._on_job_started)
        self._queue.job_progress.connect(self._on_job_progress)
        self._queue.job_finished.connect(self._on_job_finished)
        self._queue.job_failed.connect(self._on_job_failed)
        self._queue.queue_empty.connect(self._on_queue_empty)
        self._compression_engine = CompressionEngine()

        self._build_ui()
        self.setWindowTitle("VidKomp")
        self.resize(980, 720)
        self.setMinimumSize(800, 560)
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._make_sidebar())

        self._stack = QStackedWidget()
        self._stack.addWidget(self._make_process_page())
        self._compare_page = ComparePage()
        self._compare_page.status_message.connect(self._status_bar_message)
        self._stack.addWidget(self._compare_page)
        self._stack.addWidget(self._make_settings_page())
        self._stack.addWidget(self._make_system_page())
        root.addWidget(self._stack)

        self._status_bar = QStatusBar()
        self._status_bar.showMessage("Ready - drop video files to begin.")
        self.setStatusBar(self._status_bar)

    def _make_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(10, 0, 10, 20)
        layout.setSpacing(6)

        header = QWidget()
        header.setObjectName("sidebarHeader")
        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(22, 28, 22, 22)
        h_layout.setSpacing(4)

        app_title = QLabel("VidKomp")
        app_title.setObjectName("appTitle")
        app_subtitle = QLabel("Desktop video lab")
        app_subtitle.setObjectName("appSubtitle")
        h_layout.addWidget(app_title)
        h_layout.addWidget(app_subtitle)
        layout.addWidget(header)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setObjectName("sidebarDivider")
        layout.addWidget(div)
        layout.addSpacing(12)

        self._nav_buttons: list[QPushButton] = []
        nav_items = (("Process", 0), ("Compare", 1), ("Settings", 2), ("System", 3))
        for label, idx in nav_items:
            btn = NavButton(label)
            btn.setObjectName("navButton")
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _checked, i=idx: self._switch_page(i))
            layout.addWidget(btn)
            self._nav_buttons.append(btn)

        layout.addStretch()
        self._nav_buttons[0].setChecked(True)
        return sidebar

    def _switch_page(self, index: int):
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == index)
        QTimer.singleShot(0, self._refresh_hover_under_cursor)

    def warm_up_ui(self):
        """Pre-polish pages so the first interactions feel less cold."""
        self.ensurePolished()
        self.centralWidget().layout().activate()
        for idx in range(self._stack.count()):
            self._stack.setCurrentIndex(idx)
            page = self._stack.widget(idx)
            page.ensurePolished()
            if page.layout():
                page.layout().activate()
        self._stack.setCurrentIndex(0)
        for btn in self._nav_buttons:
            btn.update()

    def _refresh_hover_under_cursor(self):
        widget = QApplication.widgetAt(QCursor.pos())
        if widget is None or widget.window() is not self:
            return

        local_pos = widget.mapFromGlobal(QCursor.pos())
        posf = QPointF(local_pos)
        globalf = QPointF(QCursor.pos())
        QApplication.sendEvent(widget, QEnterEvent(posf, globalf, globalf))
        widget.update()

    def _status_bar_message(self, message: str):
        self._status_bar.showMessage(message)

    def _make_process_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("contentPage")

        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 16)
        layout.setSpacing(16)

        layout.addWidget(
            self._make_page_header(
                "Process Queue",
                "Import files, tune the queue, and launch a polished batch encode flow.",
            )
        )

        self._drop_widget = FileDropWidget()
        self._drop_widget.files_dropped.connect(self._on_files_dropped)
        layout.addWidget(self._wrap_card(self._drop_widget, "heroCard"))

        self._job_list = JobListWidget()
        self._job_list.job_remove_requested.connect(self._on_remove_job)
        layout.addWidget(self._wrap_card(self._job_list, "queueCard"), 1)

        layout.addLayout(self._make_action_bar())
        return page

    def _make_settings_page(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        layout.addWidget(
            self._make_page_header(
                "Settings",
                "Pick export defaults, codec behavior, audio strategy, and where finished files should land.",
            )
        )

        self._basic_settings = BasicSettingsPanel()
        self._advanced_settings = AdvancedSettingsPanel()
        layout.addWidget(self._basic_settings)
        layout.addWidget(self._advanced_settings)
        layout.addStretch()
        return self._wrap_scroll_page(inner)

    def _make_system_page(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        layout.addWidget(
            self._make_page_header(
                "System",
                "See the current hardware, choose CPU and GPU load profiles, and understand how hard each workload will push the machine.",
            )
        )

        self._system_panel = SystemPanel()
        layout.addWidget(self._system_panel)
        layout.addStretch()
        return self._wrap_scroll_page(inner)

    def _make_action_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(10)

        self._start_btn = QPushButton("Start Queue")
        self._start_btn.setObjectName("primaryButton")
        self._start_btn.setFixedHeight(38)
        self._start_btn.clicked.connect(self._start_queue)
        self._start_btn.setToolTip("Begin processing all pending jobs.")

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFixedHeight(38)
        self._cancel_btn.clicked.connect(self._queue.cancel_current)
        self._cancel_btn.setToolTip("Cancel the currently running job.")

        self._clear_btn = QPushButton("Clear Finished")
        self._clear_btn.setFixedHeight(38)
        self._clear_btn.clicked.connect(self._clear_finished)
        self._clear_btn.setToolTip("Remove completed and failed jobs from the list.")

        bar.addWidget(self._start_btn)
        bar.addWidget(self._cancel_btn)
        bar.addStretch()
        bar.addWidget(self._clear_btn)
        return bar

    def _make_page_header(self, title: str, subtitle: str) -> QWidget:
        header = QWidget()
        header.setObjectName("pageHeader")
        layout = QVBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("pageSubtitle")
        subtitle_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        return header

    def _wrap_card(self, widget: QWidget, object_name: str) -> QWidget:
        card = QFrame()
        card.setObjectName(object_name)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(0)
        layout.addWidget(widget)
        apply_surface_shadow(card)
        return card

    def _wrap_scroll_page(self, inner: QWidget) -> QWidget:
        page = QWidget()
        page.setObjectName("contentPage")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)

        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return page

    def _make_divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        return line

    # ------------------------------------------------------------------
    # File handling
    # ------------------------------------------------------------------

    def _on_files_dropped(self, paths: list):
        for path in paths:
            self._add_video(path)

    def _add_video(self, path: str):
        try:
            meta = VideoProbe.probe(path)
            job = VideoJob(input_path=path, source_metadata=meta)
            self._basic_settings.apply_to_job(job)
            self._advanced_settings.apply_to_job(job)
            self._system_panel.apply_to_job(job)

            output_folder = self._basic_settings.get_output_folder()
            output_format = job.output_format or "mp4"
            raw_path = FileUtils.build_output_path(
                input_path=path,
                output_folder=output_folder,
                output_format=output_format,
                suffix=FileUtils.build_workflow_suffix(job),
            )
            job.output_path = FileUtils.ensure_unique(raw_path)

            self._queue.add_job(job)
            self._job_list.add_job(
                job,
                default_mode=self._basic_settings.get_default_mode(),
                default_value=self._basic_settings.get_default_value(),
            )
            self._status_bar.showMessage(f"Added: {os.path.basename(path)}")
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Failed to load file",
                f"{os.path.basename(path)}:\n{exc}",
            )

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self._add_video(url.toLocalFile())

    def closeEvent(self, event):
        self._queue.cancel_all()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Queue control
    # ------------------------------------------------------------------

    def _start_queue(self):
        pending = [j for j in self._queue.jobs() if j.status == JobStatus.PENDING]
        if not pending:
            self._status_bar.showMessage("No pending jobs in queue.")
            return

        output_folder = self._basic_settings.get_output_folder()
        for job in pending:
            try:
                if not any(
                    (
                        job.compress_enabled,
                        job.upscale_enabled,
                        job.interpolation_enabled,
                    )
                ):
                    raise ValueError("Select at least one workflow for this file.")

                self._basic_settings.apply_to_job(job)
                self._advanced_settings.apply_to_job(job)
                self._system_panel.apply_to_job(job)

                if job.compress_enabled:
                    self._validate_compression_target(job)
                output_format = job.output_format or "mp4"
                raw_path = FileUtils.build_output_path(
                    input_path=job.input_path,
                    output_folder=output_folder,
                    output_format=output_format,
                    suffix=FileUtils.build_workflow_suffix(job),
                )
                job.output_path = FileUtils.ensure_unique(raw_path)
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Queue validation failed",
                    f"{job.display_name()}:\n{exc}",
                )
                self._status_bar.showMessage("Adjust the queue item and try again.")
                return

        self._start_btn.setEnabled(False)
        self._queue.start()

    def _validate_compression_target(self, job: VideoJob):
        meta = job.source_metadata
        if not meta:
            return

        if job.video_codec == "copy":
            raise ValueError("Video Codec 'Copy' cannot be used while compression is enabled.")

        if job.bitrate_kbps:
            return

        if job.size_mode == SizeMode.MB:
            self._compression_engine.plan_mb(
                meta,
                job.video_codec or "libx264",
                job.preset or "medium",
                job.size_value,
            )
            return

        self._compression_engine.plan_percent(
            meta,
            job.video_codec or "libx264",
            job.preset or "medium",
            job.size_value,
        )

    def _clear_finished(self):
        for job in list(self._queue.jobs()):
            if job.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED):
                self._job_list.remove_job(job)
        self._queue.clear_finished()
        self._status_bar.showMessage("Finished jobs cleared.")

    def _on_remove_job(self, job: VideoJob):
        self._queue.remove_job(job)
        self._job_list.remove_job(job)

    # ------------------------------------------------------------------
    # Queue signals
    # ------------------------------------------------------------------

    def _on_job_started(self, job: VideoJob):
        self._job_list.update_status(job)
        self._status_bar.showMessage(f"Processing: {job.display_name()}")

    def _on_job_progress(self, job: VideoJob, pct: float):
        self._job_list.update_progress(job, pct)

    def _on_job_finished(self, job: VideoJob):
        self._job_list.update_status(job)
        self._status_bar.showMessage(f"Done: {job.display_name()}")

    def _on_job_failed(self, job: VideoJob, error: str):
        self._job_list.update_status(job)
        self._status_bar.showMessage(f"Failed: {job.display_name()}")
        QMessageBox.warning(self, f"Job failed: {job.display_name()}", error)

    def _on_queue_empty(self):
        self._start_btn.setEnabled(True)
        self._status_bar.showMessage("All jobs complete.")
