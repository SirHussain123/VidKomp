"""
video_job.py
------------
VideoJob dataclass — single source of truth for one processing job.
"""

import os
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional
from core.video_probe import VideoMetadata


class JobStatus(Enum):
    PENDING   = auto()
    RUNNING   = auto()
    DONE      = auto()
    FAILED    = auto()
    CANCELLED = auto()


class InterpolationMode(Enum):
    NONE = auto()
    MINTERPOLATE_2X = auto()
    RIFE_2X = auto()


class UpscaleMode(Enum):
    NONE = auto()
    LANCZOS = auto()
    REAL_ESRGAN = auto()


class SizeMode(Enum):
    PERCENT = auto()   # reduce by X%
    MB      = auto()   # target X megabytes


@dataclass
class VideoJob:
    # --- I/O ---
    input_path:       str = ""
    output_path:      str = ""
    source_metadata:  Optional[VideoMetadata] = None

    # --- Basic output ---
    output_format:    Optional[str]   = None
    target_width:     Optional[int]   = None
    target_height:    Optional[int]   = None
    target_fps:       Optional[float] = None

    # --- Compression (always size-based) ---
    compress_enabled: bool            = True
    size_mode:        SizeMode        = SizeMode.PERCENT
    size_value:       float           = 50.0   # % or MB depending on size_mode

    # --- Advanced codec overrides ---
    video_codec:      Optional[str]   = None
    audio_codec:      Optional[str]   = None
    preset:           Optional[str]   = "medium"
    crf:              Optional[int]   = None
    bitrate_kbps:     Optional[int]   = None
    strip_audio:      bool            = False
    cpu_load:         str             = "Balanced"

    # --- Frame interpolation ---
    interpolation_enabled: bool = False
    interpolation_mode: InterpolationMode = InterpolationMode.NONE
    interpolation_model: str = "rife-v4.6"

    # --- Upscaling ---
    upscale_enabled:  bool            = False
    upscale_mode:     UpscaleMode     = UpscaleMode.NONE
    upscale_width:    Optional[int]   = None
    upscale_height:   Optional[int]   = None
    upscale_model:    str             = "realesr-animevideov3"
    upscale_scale:    int             = 2

    # --- Runtime state ---
    status:               JobStatus   = JobStatus.PENDING
    progress:             float       = 0.0
    error_message:        Optional[str] = None
    compression_reason:   str         = ""

    def display_name(self) -> str:
        return os.path.basename(self.input_path) if self.input_path else "Unnamed Job"

    def is_active(self) -> bool:
        return self.status == JobStatus.RUNNING

    def reset(self):
        self.status        = JobStatus.PENDING
        self.progress      = 0.0
        self.error_message = None
