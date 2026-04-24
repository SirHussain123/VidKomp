"""
interpolation.py
----------------
Frame interpolation logic.
Supports FFmpeg minterpolate today and external RIFE when installed.
"""

import shutil

from core.video_job import InterpolationMode, VideoJob


RIFE_MODELS = [
    "rife-v4.6",
    "rife-v4",
    "rife-anime",
    "rife-UHD",
]


class InterpolationEngine:
    """
    Configures a VideoJob for frame interpolation.
    The actual processing happens in ffmpeg_worker.py.
    """

    def apply_2x(self, job: VideoJob):
        if not job.source_metadata:
            raise ValueError("source_metadata must be set before applying interpolation.")
        job.interpolation_mode = InterpolationMode.MINTERPOLATE_2X

    def apply_rife(self, job: VideoJob, model_name: str = "rife-v4.6"):
        if not job.source_metadata:
            raise ValueError("source_metadata must be set before applying interpolation.")
        job.interpolation_mode = InterpolationMode.RIFE_2X
        job.interpolation_model = model_name

    def disable(self, job: VideoJob):
        job.interpolation_mode = InterpolationMode.NONE

    def estimated_output_fps(self, job: VideoJob) -> float | None:
        if not job.source_metadata:
            return None
        if job.interpolation_mode in (
            InterpolationMode.MINTERPOLATE_2X,
            InterpolationMode.RIFE_2X,
        ):
            return job.source_metadata.fps * 2
        return job.source_metadata.fps

    @staticmethod
    def is_rife_available() -> bool:
        return shutil.which("rife-ncnn-vulkan") is not None
