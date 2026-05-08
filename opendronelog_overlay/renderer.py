from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import sys
import time

import cv2
import numpy as np

from .config import OverlayConfig
from .csv_parser import TelemetryData
from .encoding import FfmpegEncodingConfig, FfmpegFrameEncoder, FrameEncoder

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
    telemetry_offset_s: float = 0.0,
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
        telemetry_offset_s=telemetry_offset_s,
        show_progress=show_progress,
        verbose=verbose,
    )


def _encode_transparent_overlay_frames(
    output_video_path: str,
    telemetry: TelemetryData,
    config: OverlayConfig,
    info: TransparentInfo,
    telemetry_offset_s: float,
    show_progress: bool,
    verbose: bool,
) -> None:
    codec = config.transparent_output.codec
    logger.info("Launching ffmpeg encoder (%s)", codec)
    encoder = FfmpegFrameEncoder(
        FfmpegEncodingConfig(
            output_path=output_video_path,
            width=info.width,
            height=info.height,
            fps=info.fps,
            codec=codec,
            verbose=verbose,
        )
    )

    progress_bar = ProgressReporter(
        total=info.frame_count if info.frame_count > 0 else 1,
        desc="Encoding transparent overlay",
        enabled=show_progress,
    )
    try:
        _render_overlay_frames_to_encoder(
            encoder=encoder,
            telemetry=telemetry,
            config=config,
            info=info,
            telemetry_offset_s=telemetry_offset_s,
            progress_bar=progress_bar,
        )
    finally:
        progress_bar.close()

    logger.info("Transparent overlay encoding complete")


def _render_overlay_frames_to_encoder(
    encoder: FrameEncoder,
    telemetry: TelemetryData,
    config: OverlayConfig,
    info: TransparentInfo,
    telemetry_offset_s: float,
    progress_bar: ProgressReporter,
) -> None:
    try:
        for frame_idx in range(info.frame_count):
            t_video = frame_idx / info.fps
            t_telemetry = t_video - telemetry_offset_s
            frame = np.zeros((info.height, info.width, 4), dtype=np.uint8)
            frame = _draw_overlay_rgba(frame, t_telemetry, telemetry, config)
            encoder.write(frame)
            progress_bar.update(1)
        encoder.close()
    except Exception:
        try:
            encoder.close()
        except Exception:
            pass
        raise


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

    if config.gauges.enabled:
        _draw_gauges_strip_rgba(frame, telemetry, t, config, panel.x, panel.y, panel.width, panel_h)

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


def _draw_gauges_strip_rgba(
    frame: np.ndarray,
    telemetry: TelemetryData,
    t: float,
    config: OverlayConfig,
    panel_x: int,
    panel_y: int,
    panel_w: int,
    panel_h: int,
) -> None:
    gc = config.gauges
    gh = gc.height

    # Auto placement keeps gauges aligned with the panel by default:
    # left edge matches panel and gauges are placed below the metrics card.
    auto_position = gc.x < 0
    if not auto_position:
        cx = gc.x
        cy = gc.y
        gw = gc.width
    else:
        cx = panel_x
        cy = panel_y + panel_h + 16
        gw = panel_w

    gauge_fields = [
        ("speed", "Speed", 0.0, 30.0),
        ("height", "Height", 0.0, 120.0),
        ("battery", "Battery", 0.0, 100.0),
    ]

    arc_color = _hex_to_bgra(gc.arc_color_hex, 255)
    needle_color = _hex_to_bgra(gc.needle_color_hex, 255)
    tick_color = _hex_to_bgra(gc.tick_color_hex, 200)
    label_color = _hex_to_bgra(gc.label_color_hex, 255)
    value_color = _hex_to_bgra(gc.value_color_hex, 255)

    for idx, (field, label, fallback_min, fallback_max) in enumerate(gauge_fields):
        v = _sample_numeric(telemetry, field, t)
        unit = telemetry.units.get(field, "")

        val_min = fallback_min
        val_max = fallback_max
        if v is not None and telemetry.numeric.get(field) is not None:
            data = telemetry.numeric[field]
            val_max = max(float(np.max(data)) * 1.15, fallback_max * 0.5)
            if val_max <= val_min:
                val_max = fallback_max

        gap = gc.gap
        if auto_position:
            gx = cx
            gy = cy + idx * (gh + gap)
        elif gc.layout == "horizontal":
            gx = cx + idx * (gw + gap)
            gy = cy
        else:
            gx = cx
            gy = cy + idx * (gh + gap)

        if gx + gw > frame.shape[1] or gy + gh > frame.shape[0]:
            continue

        _draw_gauge_rgba(
            frame, gx, gy, gw, gh,
            value=v, min_val=val_min, max_val=val_max,
            label=label, unit=unit,
            arc_color=arc_color, needle_color=needle_color,
            tick_color=tick_color, label_color=label_color,
            value_color=value_color,
        )


GAUGE_START_DEG = 140
GAUGE_END_DEG = 400
GAUGE_SWEEP = GAUGE_END_DEG - GAUGE_START_DEG


def _draw_gauge_rgba(
    frame: np.ndarray,
    x: int, y: int, w: int, h: int,
    value: float | None,
    min_val: float, max_val: float,
    label: str, unit: str,
    arc_color: tuple[int, int, int, int],
    needle_color: tuple[int, int, int, int],
    tick_color: tuple[int, int, int, int],
    label_color: tuple[int, int, int, int],
    value_color: tuple[int, int, int, int],
) -> None:
    _draw_rounded_panel_rgba(
        frame=frame,
        x=x,
        y=y,
        w=w,
        h=h,
        radius=max(8, int(min(w, h) * 0.1)),
        alpha=0.46,
        panel_color_hex="#000000",
    )

    wide_layout = w >= int(h * 1.35)
    cx = x + (int(w * 0.30) if wide_layout else w // 2)
    cy = y + int(h * 0.50)
    r = int(min(h * 0.34, (w * 0.22) if wide_layout else min(w, h) * 0.36))
    r = max(16, r)
    thickness = max(3, int(r * 0.18))

    if value is None or max_val <= min_val:
        norm = 0.0
        active = False
    else:
        norm = max(0.0, min(1.0, (value - min_val) / (max_val - min_val)))
        active = True

    cv2.ellipse(frame, (cx, cy), (r, r), 0, GAUGE_START_DEG, GAUGE_END_DEG, arc_color, thickness)

    if active:
        val_deg = GAUGE_START_DEG + norm * GAUGE_SWEEP
        cv2.ellipse(frame, (cx, cy), (r, r), 0, GAUGE_START_DEG, int(val_deg), needle_color, thickness)

    tick_count = 5
    inner_r = r
    outer_r = r + thickness // 2 + 3
    for i in range(tick_count + 1):
        a = math.radians(GAUGE_START_DEG + i * GAUGE_SWEEP / tick_count)
        x1 = cx + int(inner_r * math.cos(a))
        y1 = cy + int(inner_r * math.sin(a))
        x2 = cx + int(outer_r * math.cos(a))
        y2 = cy + int(outer_r * math.sin(a))
        cv2.line(frame, (x1, y1), (x2, y2), tick_color, 1, cv2.LINE_AA)

    needle_angle = math.radians(GAUGE_START_DEG + norm * GAUGE_SWEEP)
    needle_len = int(r * 0.78)
    nx = cx + int(needle_len * math.cos(needle_angle))
    ny = cy + int(needle_len * math.sin(needle_angle))
    cv2.line(frame, (cx, cy), (nx, ny), needle_color, 2, cv2.LINE_AA)

    cv2.circle(frame, (cx, cy), max(3, int(thickness * 0.35)), needle_color, -1, cv2.LINE_AA)

    if wide_layout:
        text_center_x = x + int(w * 0.68)
        label_y = y + int(h * 0.48)
        label_scale = 0.52
        value_scale = 0.62
    else:
        text_center_x = cx
        label_y = cy + r + thickness + 18
        label_scale = 0.45
        value_scale = 0.50

    label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, label_scale, 1)[0]
    cv2.putText(
        frame, label,
        (text_center_x - label_size[0] // 2, label_y),
        cv2.FONT_HERSHEY_SIMPLEX, label_scale, label_color, 1, cv2.LINE_AA,
    )

    if value is not None:
        if unit:
            val_str = f"{value:.1f} {unit}"
        else:
            val_str = f"{value:.0f}"

        val_size = cv2.getTextSize(val_str, cv2.FONT_HERSHEY_SIMPLEX, value_scale, 1)[0]
        cv2.putText(
            frame, val_str,
            (text_center_x - val_size[0] // 2, label_y + 24),
            cv2.FONT_HERSHEY_SIMPLEX, value_scale, value_color, 1, cv2.LINE_AA,
        )
    else:
        na_str = "n/a"
        na_size = cv2.getTextSize(na_str, cv2.FONT_HERSHEY_SIMPLEX, value_scale, 1)[0]
        cv2.putText(
            frame, na_str,
            (text_center_x - na_size[0] // 2, label_y + 24),
            cv2.FONT_HERSHEY_SIMPLEX, value_scale, value_color, 1, cv2.LINE_AA,
        )


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.lstrip("#")
    if len(s) != 6:
        raise ValueError(f"Invalid hex color: {hex_color}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _hex_to_bgra(hex_color: str, alpha: int) -> tuple[int, int, int, int]:
    r, g, b = _hex_to_rgb(hex_color)
    return b, g, r, max(0, min(255, int(alpha)))
