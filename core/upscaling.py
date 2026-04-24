"""
upscaling.py
------------
Upscaling logic for video jobs.
Supports FFmpeg Lanczos and external Real-ESRGAN when installed.
"""

import shutil

from core.video_job import UpscaleMode, VideoJob


UPSCALE_PRESETS = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "4K": (3840, 2160),
}

REALESRGAN_MODELS = [
    "realesr-animevideov3",
    "realesrgan-x4plus",
    "realesrgan-x4plus-anime",
    "realesrnet-x4plus",
]


class UpscalingEngine:
    """
    Configures a VideoJob for upscaling.
    Actual processing is performed in ffmpeg_worker.py.
    """

    def apply_lanczos(self, job: VideoJob, width: int, height: int):
        job.upscale_mode = UpscaleMode.LANCZOS
        job.upscale_width = width
        job.upscale_height = height

    def apply_realesrgan(
        self,
        job: VideoJob,
        width: int,
        height: int,
        scale: int = 2,
        model_name: str = "realesr-animevideov3",
    ):
        job.upscale_mode = UpscaleMode.REAL_ESRGAN
        job.upscale_width = width
        job.upscale_height = height
        job.upscale_scale = scale
        job.upscale_model = model_name

    def apply_preset(self, job: VideoJob, preset_name: str, *, mode: UpscaleMode = UpscaleMode.LANCZOS):
        if preset_name not in UPSCALE_PRESETS:
            raise KeyError(
                f"Unknown preset '{preset_name}'. Available: {list(UPSCALE_PRESETS.keys())}"
            )
        w, h = UPSCALE_PRESETS[preset_name]
        if mode == UpscaleMode.REAL_ESRGAN:
            scale = 4 if max(w, h) >= 2160 else 2
            self.apply_realesrgan(job, w, h, scale=scale)
        else:
            self.apply_lanczos(job, w, h)

    def disable(self, job: VideoJob):
        job.upscale_mode = UpscaleMode.NONE
        job.upscale_width = None
        job.upscale_height = None

    @staticmethod
    def is_realesrgan_available() -> bool:
        return shutil.which("realesrgan-ncnn-vulkan") is not None
