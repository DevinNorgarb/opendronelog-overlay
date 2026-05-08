from __future__ import annotations

import subprocess
from dataclasses import dataclass

import numpy as np
from imageio_ffmpeg import get_ffmpeg_exe


class FrameEncoder:
    """A sink for BGRA frames destined for a video container."""

    def write(self, frame_bgra: np.ndarray) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


@dataclass
class FfmpegEncodingConfig:
    output_path: str
    width: int
    height: int
    fps: float
    codec: str
    verbose: bool = False


class FfmpegFrameEncoder(FrameEncoder):
    def __init__(self, cfg: FfmpegEncodingConfig) -> None:
        ffmpeg = get_ffmpeg_exe()
        output_pix_fmt = "rgba" if cfg.codec == "png" else "argb"

        self._cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "info" if cfg.verbose else "error",
            "-nostats",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgra",
            "-s",
            f"{cfg.width}x{cfg.height}",
            "-r",
            f"{cfg.fps}",
            "-i",
            "-",
            "-an",
            "-c:v",
            cfg.codec,
            "-pix_fmt",
            output_pix_fmt,
            cfg.output_path,
        ]

        self._proc = subprocess.Popen(
            self._cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=None if cfg.verbose else subprocess.PIPE,
        )
        self._err_bytes: bytes | None = None
        self._closed = False

    def write(self, frame_bgra: np.ndarray) -> None:
        if self._closed:
            raise RuntimeError("Encoder is closed")
        if frame_bgra.dtype != np.uint8:
            raise ValueError("frame_bgra must be uint8")
        if frame_bgra.ndim != 3 or frame_bgra.shape[2] != 4:
            raise ValueError("frame_bgra must have shape (H, W, 4)")

        assert self._proc.stdin is not None
        self._proc.stdin.write(frame_bgra.tobytes())

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        try:
            assert self._proc.stdin is not None
            self._proc.stdin.close()
            _, self._err_bytes = self._proc.communicate()
        except Exception:
            self._proc.kill()
            self._proc.wait(timeout=5)
            raise

        if self._proc.returncode != 0:
            err_text = ""
            if self._err_bytes is not None:
                err_text = self._err_bytes.decode("utf-8", errors="ignore")
            raise RuntimeError(f"ffmpeg failed while writing transparent video: {err_text}")


class NullFrameEncoder(FrameEncoder):
    """Test adapter: records counts and validates shape without subprocesses."""

    def __init__(self, expected_width: int, expected_height: int) -> None:
        self.expected_width = expected_width
        self.expected_height = expected_height
        self.frame_count = 0
        self.closed = False

    def write(self, frame_bgra: np.ndarray) -> None:
        if self.closed:
            raise RuntimeError("Encoder is closed")
        if frame_bgra.shape[:2] != (self.expected_height, self.expected_width):
            raise ValueError("Unexpected frame size")
        if frame_bgra.dtype != np.uint8 or frame_bgra.shape[2] != 4:
            raise ValueError("Unexpected frame format")
        self.frame_count += 1

    def close(self) -> None:
        self.closed = True

