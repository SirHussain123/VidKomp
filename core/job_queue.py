"""
job_queue.py
------------
Manages a list of VideoJobs and drives their sequential execution.
"""

from PyQt6.QtCore import QObject, pyqtSignal
from core.video_job import VideoJob, JobStatus
from core.ffmpeg_worker import FFmpegWorker


class JobQueue(QObject):

    job_started  = pyqtSignal(object)
    job_progress = pyqtSignal(object, float)
    job_finished = pyqtSignal(object)
    job_failed   = pyqtSignal(object, str)
    queue_empty  = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs: list[VideoJob] = []
        # Keep a strong reference to every worker we create so the GC
        # never destroys a running thread. Workers are removed on completion.
        self._workers: list[FFmpegWorker] = []
        self._running = False

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def add_job(self, job: VideoJob):
        self._jobs.append(job)

    def remove_job(self, job: VideoJob):
        if job in self._jobs and job.status != JobStatus.RUNNING:
            self._jobs.remove(job)

    def clear_finished(self):
        self._jobs = [
            j for j in self._jobs
            if j.status in (JobStatus.PENDING, JobStatus.RUNNING)
        ]

    def jobs(self) -> list[VideoJob]:
        return list(self._jobs)

    # ------------------------------------------------------------------
    # Execution control
    # ------------------------------------------------------------------

    def start(self):
        if not self._running:
            self._running = True
            self._process_next()

    def stop(self):
        self._running = False

    def cancel_current(self):
        for w in self._workers:
            if w.isRunning():
                w.cancel()
                break

    def cancel_all(self):
        self._running = False
        for job in self._jobs:
            if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
                job.status = JobStatus.CANCELLED
        for worker in self._workers:
            if worker.isRunning():
                worker.cancel()
        for worker in self._workers:
            if worker.isRunning():
                worker.wait(3000)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _process_next(self):
        if not self._running:
            return

        next_job = next(
            (j for j in self._jobs if j.status == JobStatus.PENDING), None
        )

        if next_job is None:
            self._running = False
            self.queue_empty.emit()
            return

        # Pass self as parent so Qt co-owns the worker's lifetime,
        # and also keep it in _workers for an explicit strong reference.
        worker = FFmpegWorker(next_job, parent=self)
        worker.progress.connect(
            lambda pct, j=next_job: self.job_progress.emit(j, pct)
        )
        worker.job_complete.connect(self._on_job_complete)
        worker.job_failed.connect(self._on_job_failed)

        self._workers.append(worker)
        self.job_started.emit(next_job)
        worker.start()

    def _on_job_complete(self, job: VideoJob):
        self._remove_finished_workers()
        self.job_finished.emit(job)
        self._process_next()

    def _on_job_failed(self, job: VideoJob, error: str):
        self._remove_finished_workers()
        self.job_failed.emit(job, error)
        self._process_next()

    def _remove_finished_workers(self):
        """Drop references to workers whose threads have finished."""
        self._workers = [w for w in self._workers if w.isRunning()]
