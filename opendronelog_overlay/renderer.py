from __future__ import annotations

from dataclasses import dataclass
import logging
import subprocess
import sys
import time

import cv2
import numpy as np
from imageio_ffmpeg import get_ffmpeg_exe

from .config import OverlayConfig
from .csv_parser import TelemetryData

logger = logging.getLogger(__name__)


@dataclass
class TransparentInfo:
    fps: float
    width: int
    height: int
    duration_s: float
    frame_count: int


class ProgressReporter:
    def __init__(self, total: int, desc: str, enabled: bool = True) -> None:
        self.total = max(1, total)
        self.desc = desc
        self.enabled = enabled
        self.current = 0
        self.start = time.monotonic()
        self.last_render_len = 0
        self.closed = False

    def update(self, amount: int = 1) -> None:
        if not self.enabled:
            return
        self.current = min(self.total, self.current + amount)
        self._render()

    def info(self, message: str) -> None:
        if not self.enabled:
            logger.info(message)
            return

        # Clear current progress line, print info above, then redraw bar.
        sys.stderr.write("\r" + " " * self.last_render_len + "\r")
        sys.stderr.write(f"{message}\n")
        sys.stderr.flush()
        self._render()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if not self.enabled:
            return
        if self.current < self.total:
            self.current = self.total
            self._render()
        sys.stderr.write("\n")
        sys.stderr.flush()

    def _render(self) -> None:
        percent = int((self.current / self.total) * 100)
        elapsed = max(0.001, time.monotonic() - self.start)
        fps = self.current / elapsed
        width = 30
        filled = int((self.current / self.total) * width)
        bar = "#" * filled + "-" * (width - filled)
        line = (
            f"{self.desc}: {percent:3d}% [{bar}] "
            f"{self.current}/{self.total} "
            f"[{_fmt_seconds(elapsed)}, {fps:5.2f} frame/s]"
        )

        self.last_render_len = max(self.last_render_len, len(line))
        sys.stderr.write("\r" + line.ljust(self.last_render_len))
        sys.stderr.flush()


def _fmt_seconds(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def render_overlay_transparent_video(
    output_video_path: str,
    telemetry: TelemetryData,
    config: OverlayConfig,
    show_progress: bool = True,
    verbose: bool = False,
) -> None:
    out_cfg = config.transparent_output
    duration_s = float(telemetry.time_s[-1]) + out_cfg.duration_pad_s
    frame_count = max(1, int(np.ceil(duration_s * out_cfg.fps)))

    info = TransparentInfo(
        fps=out_cfg.fps,
        width=out_cfg.width,
        height=out_cfg.height,
        duration_s=duration_s,
        frame_count=frame_count,
    )

    logger.info(
        "Transparent output: %sx%s @ %.3f fps, duration=%.2fs, frames=%d",
        info.width,
        info.height,
        info.fps,
        info.duration_s,
        info.frame_count,
    )

    _encode_transparent_overlay_frames(
        output_video_path,
        telemetry,
        config,
        info,
        show_progress=show_progress,
        verbose=verbose,
    )


def _encode_transparent_overlay_frames(
    output_video_path: str,
    telemetry: TelemetryData,
    config: OverlayConfig,
    info: TransparentInfo,
    show_progress: bool,
    verbose: bool,
) -> None:
    ffmpeg = get_ffmpeg_exe()
    codec = config.transparent_output.codec
    output_pix_fmt = "rgba" if codec == "png" else "argb"

    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "info" if verbose else "error",
        "-nostats",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgra",
        "-s",
        f"{info.width}x{info.height}",
        "-r",
        f"{info.fps}",
        "-i",
        "-",
        "-an",
        "-c:v",
        codec,
        "-pix_fmt",
        output_pix_fmt,
        output_video_path,
    ]

    logger.info("Launching ffmpeg encoder (%s)", codec)

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=None if verbose else subprocess.PIPE,
    )

    progress_bar = ProgressReporter(
        total=info.frame_count if info.frame_count > 0 else 1,
        desc="Encoding transparent overlay",
        enabled=show_progress,
    )

    err_bytes: bytes | None = None
    try:
        assert proc.stdin is not None
        for frame_idx in range(info.frame_count):
            t = frame_idx / info.fps
            frame = np.zeros((info.height, info.width, 4), dtype=np.uint8)
            frame = _draw_overlay_rgba(frame, t, telemetry, config)
            proc.stdin.write(frame.tobytes())
            progress_bar.update(1)

        proc.stdin.close()
        _, err_bytes = proc.communicate()
    except Exception:
        progress_bar.close()
        proc.kill()
        proc.wait(timeout=5)
        raise
    finally:
        progress_bar.close()

    if proc.returncode != 0:
        err_text = ""
        if err_bytes is not None:
            err_text = err_bytes.decode("utf-8", errors="ignore")
        raise RuntimeError(f"ffmpeg failed while writing transparent video: {err_text}")

    logger.info("Transparent overlay encoding complete")


def _draw_overlay_rgba(frame: np.ndarray, t: float, telemetry: TelemetryData, config: OverlayConfig) -> np.ndarray:
    panel = config.video
    fields = config.telemetry.include

    row_count = len(fields)
    if config.rc_sticks.enabled:
        row_count += 4

    panel_h = panel.row_height * row_count + 24
    x, y, w, h = panel.x, panel.y, panel.width, panel_h

    if x + w > frame.shape[1]:
        w = max(80, frame.shape[1] - x - 8)
    if y + h > frame.shape[0]:
        h = max(80, frame.shape[0] - y - 8)

    _draw_rounded_panel_rgba(
        frame,
        x,
        y,
        w,
        h,
        panel.corner_radius,
        panel.opacity,
        config.style.panel_bg_hex,
    )
    label_color = _hex_to_bgra(config.style.label_text_hex, 255)
    value_color = _hex_to_bgra(config.style.value_text_hex, 255)
    muted_color = _hex_to_bgra(config.style.muted_text_hex, 255)

    y_cursor = y + 30
    for field in fields:
        line = _format_field_line(field, t, telemetry, config)
        if line is None:
            continue
        label, value = line
        cv2.putText(frame, label, (x + 14, y_cursor), cv2.FONT_HERSHEY_SIMPLEX, 0.54, label_color, 1, cv2.LINE_AA)
        text_size = cv2.getTextSize(value, cv2.FONT_HERSHEY_SIMPLEX, 0.58, 1)[0]
        cv2.putText(
            frame,
            value,
            (x + w - 14 - text_size[0], y_cursor),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            value_color,
            1,
            cv2.LINE_AA,
        )
        y_cursor += panel.row_height

    if config.rc_sticks.enabled:
        y_cursor += 8
        cv2.putText(
            frame,
            config.rc_sticks.title,
            (x + 14, y_cursor),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.46,
            muted_color,
            1,
            cv2.LINE_AA,
        )
        y_cursor += 20
        _draw_rc_sticks_rgba(frame, x + 14, y_cursor, telemetry, t, config, stick_label_color=muted_color)

    return frame


def _draw_rounded_panel_rgba(
    frame: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    radius: int,
    alpha: float,
    panel_color_hex: str,
) -> None:
    r = max(2, min(radius, w // 2, h // 2))
    a = int(max(0.0, min(alpha, 1.0)) * 255)
    color = _hex_to_bgra(panel_color_hex, a)

    cv2.rectangle(frame, (x + r, y), (x + w - r, y + h), color, -1)
    cv2.rectangle(frame, (x, y + r), (x + w, y + h - r), color, -1)

    cv2.circle(frame, (x + r, y + r), r, color, -1)
    cv2.circle(frame, (x + w - r, y + r), r, color, -1)
    cv2.circle(frame, (x + r, y + h - r), r, color, -1)
    cv2.circle(frame, (x + w - r, y + h - r), r, color, -1)


def _sample_numeric(telemetry: TelemetryData, field: str, t: float) -> float | None:
    values = telemetry.numeric.get(field)
    if values is None:
        return None
    return float(np.interp(t, telemetry.time_s, values))


def _sample_text(telemetry: TelemetryData, field: str, t: float) -> str | None:
    values = telemetry.text.get(field)
    if values is None:
        return None
    idx = int(np.searchsorted(telemetry.time_s, t, side="right") - 1)
    idx = max(0, min(idx, len(values) - 1))
    return values[idx]


def _format_field_line(
    field: str,
    t: float,
    telemetry: TelemetryData,
    config: OverlayConfig,
) -> tuple[str, str] | None:
    labels = config.telemetry.labels
    decimals = config.telemetry.decimals

    label = labels.get(field, field.replace("_", " ").title())
    precision = decimals.get(field, 1)

    if field == "flight_mode":
        mode = _sample_text(telemetry, "flight_mode", t)
        if mode is None:
            return None
        return label, mode

    v = _sample_numeric(telemetry, field, t)
    if v is None:
        return None

    unit = telemetry.units.get(field, "")
    if field in {"battery", "satellites"}:
        precision = decimals.get(field, 0)

    value = f"{v:.{precision}f}"
    if unit:
        value = f"{value} {unit}"

    return label, value


def _normalize_stick(v: float) -> float:
    # Input values often arrive in [-100..100] or [-64.5..64.5].
    if abs(v) > 1.2:
        v = v / 100.0
    return float(max(-1.0, min(1.0, v)))


def _draw_rc_sticks_rgba(
    frame: np.ndarray,
    x: int,
    y: int,
    telemetry: TelemetryData,
    t: float,
    config: OverlayConfig,
    stick_label_color: tuple[int, int, int, int],
) -> None:
    size = config.rc_sticks.size
    gap = config.rc_sticks.gap

    aileron = _normalize_stick(_sample_numeric(telemetry, "rc_aileron", t) or 0.0)
    elevator = _normalize_stick(_sample_numeric(telemetry, "rc_elevator", t) or 0.0)
    throttle = _normalize_stick(_sample_numeric(telemetry, "rc_throttle", t) or 0.0)
    rudder = _normalize_stick(_sample_numeric(telemetry, "rc_rudder", t) or 0.0)

    _draw_single_stick_rgba(frame, x, y, size, aileron, -elevator, "L", label_color=stick_label_color)
    _draw_single_stick_rgba(frame, x + size + gap, y, size, rudder, -throttle, "R", label_color=stick_label_color)


def _draw_single_stick_rgba(
    frame: np.ndarray,
    x: int,
    y: int,
    size: int,
    xv: float,
    yv: float,
    label: str,
    label_color: tuple[int, int, int, int],
) -> None:
    color_border = (87, 98, 123, 230)
    color_center = (57, 68, 94, 255)
    color_dot = (40, 199, 236, 255)

    cv2.rectangle(frame, (x, y), (x + size, y + size), color_border, 1)
    cx = x + size // 2
    cy = y + size // 2
    cv2.circle(frame, (cx, cy), 2, color_center, -1)

    r = int(size * 0.34)
    px = int(cx + xv * r)
    py = int(cy + yv * r)
    cv2.circle(frame, (px, py), 6, color_dot, -1)

    cv2.putText(
        frame,
        label,
        (x + size // 2 - 4, y + size + 17),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
            label_color,
        1,
        cv2.LINE_AA,
    )


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.lstrip("#")
    if len(s) != 6:
        raise ValueError(f"Invalid hex color: {hex_color}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _hex_to_bgra(hex_color: str, alpha: int) -> tuple[int, int, int, int]:
    r, g, b = _hex_to_rgb(hex_color)
    return b, g, r, max(0, min(255, int(alpha)))
