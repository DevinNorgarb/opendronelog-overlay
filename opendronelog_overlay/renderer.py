from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import sys
import time

import cv2
import numpy as np

from .config import OverlayComponent, OverlayConfig, ThemeConfig
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
    # New component-based path (preferred when present).
    if config.components:
        _draw_components_rgba(frame, t, telemetry, config)
        return frame

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


def _draw_components_rgba(frame: np.ndarray, t: float, telemetry: TelemetryData, config: OverlayConfig) -> None:
    theme = config.theme
    for comp in config.components:
        _draw_component_rgba(frame, t, telemetry, config, theme, comp)


def _draw_component_rgba(
    frame: np.ndarray,
    t: float,
    telemetry: TelemetryData,
    overlay_cfg: OverlayConfig,
    theme: ThemeConfig,
    comp: OverlayComponent,
) -> None:
    r = comp.rect
    x, y, w, h = r.x, r.y, r.w, r.h
    if w <= 0 or h <= 0:
        return
    if x >= frame.shape[1] or y >= frame.shape[0]:
        return
    if x + w <= 0 or y + h <= 0:
        return

    if comp.type == "value_card":
        _draw_value_card_component_rgba(frame, x, y, w, h, t, telemetry, overlay_cfg, theme, comp)
        return
    if comp.type == "rc_sticks":
        _draw_rc_sticks_component_rgba(frame, x, y, w, h, t, telemetry, overlay_cfg, theme, comp)
        return
    if comp.type == "dial_gauge":
        _draw_dial_gauge_component_rgba(frame, x, y, w, h, t, telemetry, overlay_cfg, theme, comp)
        return
    if comp.type == "sparkline":
        _draw_sparkline_component_rgba(frame, x, y, w, h, t, telemetry, overlay_cfg, theme, comp)
        return
    if comp.type == "compass":
        _draw_compass_component_rgba(frame, x, y, w, h, t, telemetry, overlay_cfg, theme, comp)
        return


def _theme_hex(theme: ThemeConfig, comp: OverlayComponent, key: str) -> str:
    v = comp.style.get(key)
    if isinstance(v, str) and v.strip():
        return v
    return getattr(theme, key)


def _draw_value_card_component_rgba(
    frame: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    t: float,
    telemetry: TelemetryData,
    overlay_cfg: OverlayConfig,
    theme: ThemeConfig,
    comp: OverlayComponent,
) -> None:
    fields = comp.config.get("fields")
    if not isinstance(fields, list) or not fields:
        fields = overlay_cfg.telemetry.include

    row_h = int(comp.config.get("row_height", overlay_cfg.video.row_height) or overlay_cfg.video.row_height)
    row_h = max(16, row_h)
    padding = int(comp.config.get("padding", 14) or 14)
    padding = max(6, padding)
    corner = int(comp.config.get("corner_radius", overlay_cfg.video.corner_radius) or overlay_cfg.video.corner_radius)
    opacity = float(comp.config.get("opacity", overlay_cfg.video.opacity) or overlay_cfg.video.opacity)

    panel_bg = _theme_hex(theme, comp, "panel_bg_hex")
    label_hex = _theme_hex(theme, comp, "label_text_hex")
    value_hex = _theme_hex(theme, comp, "value_text_hex")
    muted_hex = _theme_hex(theme, comp, "muted_text_hex")

    _draw_rounded_panel_rgba(frame, x, y, w, h, corner, opacity, panel_bg)

    label_color = _hex_to_bgra(label_hex, 255)
    value_color = _hex_to_bgra(value_hex, 255)
    muted_color = _hex_to_bgra(muted_hex, 255)

    title = comp.config.get("title")
    y_cursor = y + padding + 2
    if isinstance(title, str) and title.strip():
        cv2.putText(frame, title, (x + padding, y_cursor + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.46, muted_color, 1, cv2.LINE_AA)
        y_cursor += 22

    for field in fields:
        if not isinstance(field, str):
            continue
        line = _format_field_line(field, t, telemetry, overlay_cfg)
        if line is None:
            continue
        label, value = line
        cv2.putText(frame, label, (x + padding, y_cursor), cv2.FONT_HERSHEY_SIMPLEX, 0.54, label_color, 1, cv2.LINE_AA)
        text_size = cv2.getTextSize(value, cv2.FONT_HERSHEY_SIMPLEX, 0.58, 1)[0]
        cv2.putText(
            frame,
            value,
            (x + w - padding - text_size[0], y_cursor),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            value_color,
            1,
            cv2.LINE_AA,
        )
        y_cursor += row_h


def _draw_rc_sticks_component_rgba(
    frame: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    t: float,
    telemetry: TelemetryData,
    overlay_cfg: OverlayConfig,
    theme: ThemeConfig,
    comp: OverlayComponent,
) -> None:
    corner = int(comp.config.get("corner_radius", 12) or 12)
    opacity = float(comp.config.get("opacity", 0.46) or 0.46)
    panel_bg = comp.style.get("panel_bg_hex")
    if not isinstance(panel_bg, str) or not panel_bg.strip():
        panel_bg = "#000000"
    _draw_rounded_panel_rgba(frame, x, y, w, h, corner, opacity, panel_bg)

    label_hex = _theme_hex(theme, comp, "muted_text_hex")
    label_color = _hex_to_bgra(label_hex, 255)

    title = comp.config.get("title", overlay_cfg.rc_sticks.title)
    if not isinstance(title, str):
        title = overlay_cfg.rc_sticks.title
    cv2.putText(frame, title, (x + 12, y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.46, label_color, 1, cv2.LINE_AA)

    # Draw sticks inside the rect with a simple layout.
    size = int(comp.config.get("size", overlay_cfg.rc_sticks.size) or overlay_cfg.rc_sticks.size)
    size = max(32, min(size, min(w, h) - 18))
    gap = int(comp.config.get("gap", overlay_cfg.rc_sticks.gap) or overlay_cfg.rc_sticks.gap)
    gap = max(0, gap)

    sticks_y = y + 34
    sticks_x = x + 12
    _draw_rc_sticks_rgba(frame, sticks_x, sticks_y, telemetry, t, overlay_cfg, stick_label_color=label_color)


def _draw_dial_gauge_component_rgba(
    frame: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    t: float,
    telemetry: TelemetryData,
    overlay_cfg: OverlayConfig,
    theme: ThemeConfig,
    comp: OverlayComponent,
) -> None:
    field = comp.config.get("field")
    if not isinstance(field, str) or not field.strip():
        field = "speed"
    label = comp.config.get("label")
    if not isinstance(label, str) or not label.strip():
        label = field.replace("_", " ").title()

    v = _sample_numeric(telemetry, field, t)
    unit = telemetry.units.get(field, "")

    fallback_min = float(comp.config.get("min", 0.0) or 0.0)
    fallback_max = float(comp.config.get("max", 100.0) or 100.0)
    val_min = fallback_min
    val_max = fallback_max
    if v is not None and telemetry.numeric.get(field) is not None:
        data = telemetry.numeric[field]
        val_max = max(float(np.max(data)) * 1.15, fallback_max * 0.5)
        if val_max <= val_min:
            val_max = fallback_max

    arc = comp.style.get("arc_hex")
    if not isinstance(arc, str) or not arc.strip():
        arc = theme.arc_hex
    tick = comp.style.get("tick_hex")
    if not isinstance(tick, str) or not tick.strip():
        tick = theme.tick_hex
    accent = comp.style.get("accent_hex")
    if not isinstance(accent, str) or not accent.strip():
        accent = theme.accent_hex
    label_hex = _theme_hex(theme, comp, "label_text_hex")
    value_hex = _theme_hex(theme, comp, "value_text_hex")

    _draw_gauge_rgba(
        frame,
        x,
        y,
        w,
        h,
        value=v,
        min_val=val_min,
        max_val=val_max,
        label=label,
        unit=unit,
        arc_color=_hex_to_bgra(arc, 255),
        needle_color=_hex_to_bgra(accent, 255),
        tick_color=_hex_to_bgra(tick, 200),
        label_color=_hex_to_bgra(label_hex, 255),
        value_color=_hex_to_bgra(value_hex, 255),
    )


def _draw_sparkline_component_rgba(
    frame: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    t: float,
    telemetry: TelemetryData,
    overlay_cfg: OverlayConfig,
    theme: ThemeConfig,
    comp: OverlayComponent,
) -> None:
    field = comp.config.get("field")
    if not isinstance(field, str) or not field.strip():
        field = "speed"

    window_s = float(comp.config.get("window_s", 5.0) or 5.0)
    window_s = max(0.5, window_s)

    corner = int(comp.config.get("corner_radius", 12) or 12)
    opacity = float(comp.config.get("opacity", 0.46) or 0.46)
    panel_bg = comp.style.get("panel_bg_hex")
    if not isinstance(panel_bg, str) or not panel_bg.strip():
        panel_bg = "#000000"
    _draw_rounded_panel_rgba(frame, x, y, w, h, corner, opacity, panel_bg)

    accent = comp.style.get("accent_hex")
    if not isinstance(accent, str) or not accent.strip():
        accent = theme.accent_hex

    values = telemetry.numeric.get(field)
    if values is None:
        na = _hex_to_bgra(theme.muted_text_hex, 255)
        cv2.putText(frame, "n/a", (x + 12, y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, na, 1, cv2.LINE_AA)
        return

    t0 = t - window_s
    t1 = t
    # Sample N points evenly in time.
    n = int(comp.config.get("samples", 60) or 60)
    n = max(10, min(300, n))
    ts = np.linspace(t0, t1, n)
    ys = np.interp(ts, telemetry.time_s, values)

    y_min = float(comp.config.get("y_min")) if isinstance(comp.config.get("y_min"), (int, float)) else float(np.min(ys))
    y_max = float(comp.config.get("y_max")) if isinstance(comp.config.get("y_max"), (int, float)) else float(np.max(ys))
    if y_max <= y_min:
        y_max = y_min + 1.0

    pad = 10
    x0 = x + pad
    y0 = y + pad
    x1 = x + w - pad
    y1 = y + h - pad
    if x1 <= x0 or y1 <= y0:
        return

    pts: list[tuple[int, int]] = []
    for i in range(n):
        px = int(x0 + (i / (n - 1)) * (x1 - x0))
        norm = (ys[i] - y_min) / (y_max - y_min)
        py = int(y1 - norm * (y1 - y0))
        pts.append((px, py))
    for i in range(1, len(pts)):
        cv2.line(frame, pts[i - 1], pts[i], _hex_to_bgra(accent, 255), 2, cv2.LINE_AA)

    label = comp.config.get("label")
    if isinstance(label, str) and label.strip():
        txt = _hex_to_bgra(theme.muted_text_hex, 255)
        cv2.putText(frame, label, (x + 12, y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.46, txt, 1, cv2.LINE_AA)


def _draw_compass_component_rgba(
    frame: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    t: float,
    telemetry: TelemetryData,
    overlay_cfg: OverlayConfig,
    theme: ThemeConfig,
    comp: OverlayComponent,
) -> None:
    field = comp.config.get("field")
    if not isinstance(field, str) or not field.strip():
        field = "heading_deg"

    corner = int(comp.config.get("corner_radius", 12) or 12)
    opacity = float(comp.config.get("opacity", 0.46) or 0.46)
    panel_bg = comp.style.get("panel_bg_hex")
    if not isinstance(panel_bg, str) or not panel_bg.strip():
        panel_bg = "#000000"
    _draw_rounded_panel_rgba(frame, x, y, w, h, corner, opacity, panel_bg)

    heading = _sample_numeric(telemetry, field, t)
    if heading is None:
        na = _hex_to_bgra(theme.muted_text_hex, 255)
        cv2.putText(frame, "n/a", (x + 12, y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, na, 1, cv2.LINE_AA)
        return

    accent = comp.style.get("accent_hex")
    if not isinstance(accent, str) or not accent.strip():
        accent = theme.accent_hex

    # Simple heading tape: center arrow pointing up, with degrees text.
    txt = _hex_to_bgra(theme.value_text_hex, 255)
    muted = _hex_to_bgra(theme.muted_text_hex, 255)
    cx = x + w // 2
    cy = y + h // 2
    r = max(14, min(w, h) // 3)
    cv2.circle(frame, (cx, cy), r, _hex_to_bgra(theme.tick_hex, 180), 2, cv2.LINE_AA)
    # Arrow pointing to heading (0 = north/up)
    ang = np.deg2rad(float(heading) - 90.0)
    ax = int(cx + r * 0.9 * np.cos(ang))
    ay = int(cy + r * 0.9 * np.sin(ang))
    cv2.line(frame, (cx, cy), (ax, ay), _hex_to_bgra(accent, 255), 3, cv2.LINE_AA)
    cv2.circle(frame, (cx, cy), 3, _hex_to_bgra(accent, 255), -1, cv2.LINE_AA)

    deg = f"{heading:05.1f}°"
    deg_size = cv2.getTextSize(deg, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)[0]
    cv2.putText(frame, deg, (cx - deg_size[0] // 2, y + h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.55, txt, 1, cv2.LINE_AA)

    label = comp.config.get("label")
    if not isinstance(label, str) or not label.strip():
        label = "Heading"
    label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.46, 1)[0]
    cv2.putText(frame, label, (cx - label_size[0] // 2, y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.46, muted, 1, cv2.LINE_AA)


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
