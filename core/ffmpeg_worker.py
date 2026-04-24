"""
ffmpeg_worker.py
----------------
QThread worker that can compress, enhance, or run both in one pipeline.
External Real-ESRGAN and RIFE paths are used when installed and selected.
"""

import logging
import os
import shutil
import subprocess
import threading

from PyQt6.QtCore import QThread, pyqtSignal

from core.compression import CompressionEngine, CompressionPlan
from core.video_job import (
    InterpolationMode,
    JobStatus,
    SizeMode,
    UpscaleMode,
    VideoJob,
)
from utils.file_utils import FileUtils

log = logging.getLogger(__name__)

FORMAT_DEFAULT_CODEC = {
    "mp4": "libx264",
    "mkv": "libx264",
    "avi": "libx264",
    "mov": "libx264",
    "webm": "libvpx-vp9",
    "flv": "libx264",
    "wmv": "wmv2",
}

NO_PRESET_CODECS = {"libvpx-vp9", "libaom-av1", "wmv2", "copy"}
CRF_SPECIAL = {"libvpx-vp9", "libaom-av1"}
CPU_THREADS = {
    "Low": 2,
    "Balanced": 4,
    "High": 8,
    "Maximum": 0,
}


class FFmpegWorker(QThread):
    progress = pyqtSignal(float)
    job_complete = pyqtSignal(object)
    job_failed = pyqtSignal(object, str)

    def __init__(self, job: VideoJob, parent=None):
        super().__init__(parent)
        self.job = job
        self._process: subprocess.Popen | None = None

    def run(self):
        self.job.status = JobStatus.RUNNING
        try:
            if self._uses_external_enhancement():
                self._run_external_pipeline()
            elif self.job.compress_enabled:
                plan = self._resolve_plan()
                self.job.compression_reason = plan.reason
                log.info("Plan: %s", plan.reason)
                self._run_two_pass(plan)
            else:
                self.job.compression_reason = "Enhancement-only pipeline."
                self._run_single_pass()
        except Exception as exc:
            log.exception("Worker exception for %s", self.job.input_path)
            self._fail(str(exc))

    def cancel(self):
        if self._process and self._process.poll() is None:
            self._process.terminate()
            self.job.status = JobStatus.CANCELLED

    def _uses_external_enhancement(self) -> bool:
        return (
            self.job.upscale_mode == UpscaleMode.REAL_ESRGAN
            or self.job.interpolation_mode == InterpolationMode.RIFE_2X
        )

    def _resolve_plan(self) -> CompressionPlan:
        job = self.job
        meta = job.source_metadata
        fmt = (job.output_format or "mp4").lower()
        codec = job.video_codec or FORMAT_DEFAULT_CODEC.get(fmt, "libx264")
        preset = job.preset or "medium"
        engine = CompressionEngine()

        if not meta:
            raise ValueError("No source metadata - cannot plan compression.")

        if codec == "copy":
            raise ValueError("Video Codec 'Copy' cannot be used while compression is enabled.")

        if job.bitrate_kbps:
            return CompressionPlan(
                codec=codec,
                preset=preset,
                two_pass=True,
                target_bitrate_kbps=job.bitrate_kbps,
                reason=f"Forced target bitrate - {job.bitrate_kbps} kbps video bitrate. Two-pass.",
            )

        if job.size_mode == SizeMode.MB:
            return engine.plan_mb(meta, codec, preset, job.size_value)
        return engine.plan_percent(meta, codec, preset, job.size_value)

    def _run_two_pass(self, plan: CompressionPlan):
        import tempfile

        passlogfile = os.path.join(tempfile.gettempdir(), f"compressor_{id(self)}")
        try:
            cmd1 = self._build_two_pass_cmd(
                plan,
                pass_num=1,
                passlogfile=passlogfile,
                input_path=self.job.input_path,
                vf_filters=self._vf_filters(),
                output_path=self.job.output_path,
            )
            log.info("Pass 1: %s", " ".join(cmd1))
            self._run_process(cmd1, progress_offset=0.0, progress_scale=0.4)

            if self.job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
                return

            cmd2 = self._build_two_pass_cmd(
                plan,
                pass_num=2,
                passlogfile=passlogfile,
                input_path=self.job.input_path,
                vf_filters=self._vf_filters(),
                output_path=self.job.output_path,
            )
            log.info("Pass 2: %s", " ".join(cmd2))
            self._run_process(cmd2, progress_offset=40.0, progress_scale=0.6)

            if self.job.status != JobStatus.FAILED:
                self._mark_complete()
        finally:
            for ext in ("", ".log", ".log.mbtree", "-0.log", "-0.log.mbtree"):
                try:
                    os.remove(passlogfile + ext)
                except FileNotFoundError:
                    pass

    def _run_single_pass(self):
        vf_filters = self._vf_filters()
        if not vf_filters and not self._can_stream_copy_video():
            raise ValueError("Select at least one workflow before starting the queue.")

        cmd = self._build_single_pass_cmd(
            input_path=self.job.input_path,
            output_path=self.job.output_path,
            vf_filters=vf_filters,
        )
        log.info("Single pass: %s", " ".join(cmd))
        self._run_process(cmd, progress_offset=0.0, progress_scale=1.0)

        if self.job.status != JobStatus.FAILED:
            self._mark_complete()

    def _run_external_pipeline(self):
        temp_root = FileUtils.create_temp_dir("compressor_enhance_")
        try:
            frame_input_dir = os.path.join(temp_root, "frames_in")
            os.makedirs(frame_input_dir, exist_ok=True)
            frame_pattern = "frame_%08d.png"

            self._extract_frames(self.job.input_path, frame_input_dir, frame_pattern)

            if self.job.upscale_mode == UpscaleMode.REAL_ESRGAN:
                frame_output_dir = os.path.join(temp_root, "frames_upscaled")
                os.makedirs(frame_output_dir, exist_ok=True)
                self._run_realesrgan(frame_input_dir, frame_output_dir)
                frame_input_dir = frame_output_dir

            if self.job.interpolation_mode == InterpolationMode.RIFE_2X:
                frame_output_dir = os.path.join(temp_root, "frames_interpolated")
                os.makedirs(frame_output_dir, exist_ok=True)
                frame_pattern = "%08d.png"
                self._run_rife(frame_input_dir, frame_output_dir, frame_pattern)
                frame_input_dir = frame_output_dir

            assemble_filters = self._external_pipeline_filters()
            assemble_fps = self._assembly_fps()

            if self.job.compress_enabled:
                intermediate_path = os.path.join(temp_root, "enhanced.mp4")
                self._assemble_video_from_frames(
                    frames_dir=frame_input_dir,
                    frame_pattern=frame_pattern,
                    output_path=intermediate_path,
                    fps=assemble_fps,
                    vf_filters=assemble_filters,
                    final_audio=False,
                    video_codec="libx264",
                )
                plan = self._resolve_plan()
                self.job.compression_reason = f"{plan.reason} External enhancement pipeline."
                self._run_two_pass_from_source(plan, intermediate_path)
            else:
                final_codec = self.job.video_codec or FORMAT_DEFAULT_CODEC.get(
                    (self.job.output_format or "mp4").lower(),
                    "libx264",
                )
                if final_codec == "copy":
                    final_codec = FORMAT_DEFAULT_CODEC.get(
                        (self.job.output_format or "mp4").lower(),
                        "libx264",
                    )
                self._assemble_video_from_frames(
                    frames_dir=frame_input_dir,
                    frame_pattern=frame_pattern,
                    output_path=self.job.output_path,
                    fps=assemble_fps,
                    vf_filters=assemble_filters,
                    final_audio=True,
                    video_codec=final_codec,
                )
                self._mark_complete()
        finally:
            FileUtils.cleanup_temp_dir(temp_root)

    def _run_two_pass_from_source(self, plan: CompressionPlan, input_path: str):
        import tempfile

        passlogfile = os.path.join(tempfile.gettempdir(), f"compressor_{id(self)}_ext")
        try:
            cmd1 = self._build_two_pass_cmd(
                plan,
                pass_num=1,
                passlogfile=passlogfile,
                input_path=input_path,
                vf_filters=[],
                output_path=self.job.output_path,
            )
            self._run_process(cmd1, progress_offset=88.0, progress_scale=0.04)
            if self.job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
                return

            cmd2 = self._build_two_pass_cmd(
                plan,
                pass_num=2,
                passlogfile=passlogfile,
                input_path=input_path,
                vf_filters=[],
                output_path=self.job.output_path,
            )
            self._run_process(cmd2, progress_offset=92.0, progress_scale=0.08)
            if self.job.status != JobStatus.FAILED:
                self._mark_complete()
        finally:
            for ext in ("", ".log", ".log.mbtree", "-0.log", "-0.log.mbtree"):
                try:
                    os.remove(passlogfile + ext)
                except FileNotFoundError:
                    pass

    def _build_two_pass_cmd(
        self,
        plan: CompressionPlan,
        pass_num: int,
        passlogfile: str,
        *,
        input_path: str,
        vf_filters: list[str],
        output_path: str,
    ) -> list[str]:
        codec = plan.codec
        cmd = ["ffmpeg", "-y", "-i", input_path]

        if vf_filters:
            cmd += ["-vf", ",".join(vf_filters)]

        cmd += ["-c:v", codec, "-b:v", f"{plan.target_bitrate_kbps}k"]
        cmd += self._thread_args()
        if codec in CRF_SPECIAL:
            cmd += ["-crf", "10"]
        if plan.preset and codec not in NO_PRESET_CODECS:
            cmd += ["-preset", plan.preset]

        if pass_num == 1:
            cmd += ["-pass", "1", "-passlogfile", passlogfile, "-an", "-f", "null"]
            cmd.append("NUL" if os.name == "nt" else "/dev/null")
            return cmd

        cmd += ["-pass", "2", "-passlogfile", passlogfile]
        cmd += self._audio_args()
        cmd += ["-progress", "pipe:1", "-nostats", output_path]
        return cmd

    def _build_single_pass_cmd(
        self,
        *,
        input_path: str,
        output_path: str,
        vf_filters: list[str],
    ) -> list[str]:
        job = self.job
        fmt = (job.output_format or "mp4").lower()
        codec = job.video_codec or FORMAT_DEFAULT_CODEC.get(fmt, "libx264")
        preset = job.preset or "medium"

        cmd = ["ffmpeg", "-y", "-i", input_path]
        if vf_filters:
            cmd += ["-vf", ",".join(vf_filters)]

        if codec == "copy":
            if vf_filters:
                raise ValueError(
                    "Video Codec 'Copy' cannot be used with upscaling, frame generation, resizing, or FPS changes."
                )
            cmd += ["-c:v", "copy"]
            cmd += self._audio_args()
            cmd += ["-progress", "pipe:1", "-nostats", output_path]
            return cmd

        cmd += ["-c:v", codec]
        cmd += self._thread_args()
        if job.bitrate_kbps:
            cmd += ["-b:v", f"{job.bitrate_kbps}k"]
        elif job.crf is not None:
            cmd += ["-crf", str(job.crf)]
        elif codec in CRF_SPECIAL:
            cmd += ["-crf", "18"]
        elif codec not in NO_PRESET_CODECS:
            cmd += ["-crf", "18"]

        if preset and codec not in NO_PRESET_CODECS:
            cmd += ["-preset", preset]

        cmd += self._audio_args()
        cmd += ["-progress", "pipe:1", "-nostats", output_path]
        return cmd

    def _audio_args(self) -> list[str]:
        job = self.job
        if job.strip_audio:
            return ["-an"]
        if job.audio_codec and job.audio_codec != "copy":
            return ["-c:a", job.audio_codec]
        return ["-c:a", "copy"]

    def _thread_args(self) -> list[str]:
        threads = CPU_THREADS.get(self.job.cpu_load, CPU_THREADS["Balanced"])
        if threads > 0:
            return ["-threads", str(threads)]
        return []

    def _vf_filters(self) -> list[str]:
        job = self.job
        vf: list[str] = []
        w = job.upscale_width or job.target_width
        h = job.upscale_height or job.target_height
        if w and h and job.upscale_mode != UpscaleMode.REAL_ESRGAN:
            w += w % 2
            h += h % 2
            vf.append(f"scale={w}:{h}:flags=lanczos")
        if job.target_fps:
            vf.append(f"fps={job.target_fps}")
        if job.interpolation_mode == InterpolationMode.MINTERPOLATE_2X:
            src_fps = job.source_metadata.fps if job.source_metadata else 30
            vf.append(
                f"minterpolate=fps={src_fps * 2}:mi_mode=mci:"
                f"mc_mode=aobmc:me_mode=bidir:vsbmc=1"
            )
        return vf

    def _external_pipeline_filters(self) -> list[str]:
        vf: list[str] = []
        job = self.job
        if job.upscale_mode != UpscaleMode.REAL_ESRGAN and job.target_width and job.target_height:
            vf.append(f"scale={job.target_width}:{job.target_height}:flags=lanczos")
        elif (
            job.upscale_mode == UpscaleMode.REAL_ESRGAN
            and job.upscale_width
            and job.upscale_height
            and (job.upscale_width != job.target_width or job.upscale_height != job.target_height)
            and job.target_width
            and job.target_height
        ):
            vf.append(f"scale={job.target_width}:{job.target_height}:flags=lanczos")

        if job.target_fps:
            vf.append(f"fps={job.target_fps}")
        return vf

    def _assembly_fps(self) -> float:
        job = self.job
        if job.interpolation_mode == InterpolationMode.RIFE_2X and job.source_metadata:
            return job.source_metadata.fps * 2
        if job.source_metadata:
            return job.source_metadata.fps
        return 30.0

    def _can_stream_copy_video(self) -> bool:
        return (
            not self.job.compress_enabled
            and not self._vf_filters()
            and self.job.video_codec == "copy"
        )

    def _extract_frames(self, input_path: str, output_dir: str, pattern: str):
        output_pattern = os.path.join(output_dir, pattern)
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-vsync",
            "0",
            "-progress",
            "pipe:1",
            "-nostats",
            output_pattern,
        ]
        self._run_process(cmd, progress_offset=0.0, progress_scale=0.18)

    def _run_realesrgan(self, input_dir: str, output_dir: str):
        if not self.job.upscale_width or not self.job.upscale_height:
            raise ValueError("Real-ESRGAN requires a target upscale size.")
        if shutil.which("realesrgan-ncnn-vulkan") is None:
            raise ValueError("Real-ESRGAN is selected but 'realesrgan-ncnn-vulkan' is not available on PATH.")

        cmd = [
            "realesrgan-ncnn-vulkan",
            "-i",
            input_dir,
            "-o",
            output_dir,
            "-s",
            str(self.job.upscale_scale),
            "-n",
            self.job.upscale_model,
            "-f",
            "png",
        ]
        self._run_process(cmd, progress_offset=0.18, progress_scale=0.34, use_duration=False)

    def _run_rife(self, input_dir: str, output_dir: str, output_pattern: str):
        cmd = [
            "rife-ncnn-vulkan",
            "-i",
            input_dir,
            "-o",
            output_dir,
            "-m",
            self.job.interpolation_model,
            "-f",
            output_pattern,
        ]
        if shutil.which("rife-ncnn-vulkan") is None:
            raise ValueError("RIFE is selected but 'rife-ncnn-vulkan' is not available on PATH.")
        self._run_process(cmd, progress_offset=0.52, progress_scale=0.28, use_duration=False)

    def _assemble_video_from_frames(
        self,
        *,
        frames_dir: str,
        frame_pattern: str,
        output_path: str,
        fps: float,
        vf_filters: list[str],
        final_audio: bool,
        video_codec: str,
    ):
        input_pattern = os.path.join(frames_dir, frame_pattern)
        cmd = [
            "ffmpeg",
            "-y",
            "-framerate",
            f"{fps}",
            "-i",
            input_pattern,
            "-i",
            self.job.input_path,
            "-map",
            "0:v:0",
            "-map",
            "1:a?",
        ]
        if vf_filters:
            cmd += ["-vf", ",".join(vf_filters)]
        cmd += ["-c:v", video_codec]
        if video_codec not in NO_PRESET_CODECS:
            cmd += ["-crf", "12", "-preset", "medium"]
        elif video_codec in CRF_SPECIAL:
            cmd += ["-crf", "12"]
        cmd += ["-pix_fmt", "yuv420p"]
        if final_audio:
            cmd += self._audio_args()
        else:
            cmd += ["-c:a", "copy"]
        cmd += ["-shortest", "-progress", "pipe:1", "-nostats", output_path]
        self._run_process(
            cmd,
            progress_offset=80.0,
            progress_scale=0.20 if not self.job.compress_enabled else 0.08,
        )

    def _run_process(
        self,
        cmd: list[str],
        progress_offset: float,
        progress_scale: float,
        *,
        use_duration: bool = True,
    ):
        duration = self.job.source_metadata.duration if (self.job.source_metadata and use_duration) else None

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        stderr_lines: list[str] = []

        def _drain():
            for line in self._process.stderr:
                stderr_lines.append(line)

        t = threading.Thread(target=_drain, daemon=True)
        t.start()

        for line in self._process.stdout:
            if line.startswith("out_time_ms=") and duration:
                try:
                    elapsed_s = int(line.strip().split("=")[1]) / 1_000_000
                    raw_pct = min(100.0, (elapsed_s / duration) * 100)
                    pct = progress_offset + raw_pct * progress_scale
                    self.job.progress = pct
                    self.progress.emit(pct)
                except ValueError:
                    pass

        self._process.wait()
        t.join()

        if self._process.returncode != 0:
            self._fail("".join(stderr_lines) or f"Command failed: {' '.join(cmd)}")
        elif progress_scale > 0 and duration is None:
            pct = min(100.0, progress_offset + progress_scale)
            self.job.progress = pct
            self.progress.emit(pct)

    def _mark_complete(self):
        self.job.status = JobStatus.DONE
        self.job.progress = 100.0
        self.progress.emit(100.0)
        self.job_complete.emit(self.job)
        log.info("Done: %s -> %s", self.job.input_path, self.job.output_path)

    def _fail(self, message: str):
        self.job.status = JobStatus.FAILED
        self.job.error_message = message
        log.error("Failed: %s\n%s", self.job.input_path, message)
        self.job_failed.emit(self.job, message)
