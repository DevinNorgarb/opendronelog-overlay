from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

import yaml


@dataclass
class VideoPanelConfig:
    x: int = 28
    y: int = 28
    width: int = 260
    row_height: int = 30
    opacity: float = 0.58
    corner_radius: int = 14


@dataclass
class TransparentOutputConfig:
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    duration_pad_s: float = 0.0
    # png and qtrle preserve alpha in .mov containers. png is usually safer.
    codec: str = "png"


@dataclass
class StyleConfig:
    panel_bg_hex: str = "#1E2434"
    label_text_hex: str = "#C8CDDC"
    value_text_hex: str = "#EFF3F8"
    muted_text_hex: str = "#AAB2C2"


@dataclass
class TelemetryConfig:
    include: list[str] = field(
        default_factory=lambda: [
            "height",
            "speed",
            "distance_to_home",
            "battery",
            "satellites",
            "lat",
            "lng",
            "flight_mode",
        ]
    )
    labels: dict[str, str] = field(default_factory=dict)
    decimals: dict[str, int] = field(default_factory=dict)
    unit_system: str = "auto"


@dataclass
class RcSticksConfig:
    enabled: bool = True
    title: str = "RC STICKS"
    size: int = 54
    gap: int = 12


@dataclass
class OverlayConfig:
    video: VideoPanelConfig = field(default_factory=VideoPanelConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    rc_sticks: RcSticksConfig = field(default_factory=RcSticksConfig)
    transparent_output: TransparentOutputConfig = field(default_factory=TransparentOutputConfig)
    style: StyleConfig = field(default_factory=StyleConfig)


VALID_FIELDS = {
    "height",
    "speed",
    "distance_to_home",
    "battery",
    "satellites",
    "lat",
    "lng",
    "flight_mode",
    "altitude",
    "battery_voltage",
    "battery_temp",
}


def _merge_dict(base: dict[str, Any], custom: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in custom.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | Path | None) -> OverlayConfig:
    default_cfg = OverlayConfig()
    if path is None:
        return default_cfg

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    merged = _merge_dict(
        {
            "video": default_cfg.video.__dict__,
            "telemetry": default_cfg.telemetry.__dict__,
            "rc_sticks": default_cfg.rc_sticks.__dict__,
            "transparent_output": default_cfg.transparent_output.__dict__,
            "style": default_cfg.style.__dict__,
        },
        raw,
    )

    cfg = OverlayConfig(
        video=VideoPanelConfig(**merged["video"]),
        telemetry=TelemetryConfig(**merged["telemetry"]),
        rc_sticks=RcSticksConfig(**merged["rc_sticks"]),
        transparent_output=TransparentOutputConfig(**merged["transparent_output"]),
        style=StyleConfig(**merged["style"]),
    )

    unknown = set(cfg.telemetry.include) - VALID_FIELDS
    if unknown:
        raise ValueError(f"Unsupported telemetry field(s): {sorted(unknown)}")

    if cfg.telemetry.unit_system not in {"auto", "metric", "imperial"}:
        raise ValueError("telemetry.unit_system must be one of: auto, metric, imperial")

    if cfg.transparent_output.width <= 0 or cfg.transparent_output.height <= 0:
        raise ValueError("transparent_output.width and transparent_output.height must be > 0")

    if cfg.transparent_output.fps <= 0:
        raise ValueError("transparent_output.fps must be > 0")

    if cfg.transparent_output.duration_pad_s < 0:
        raise ValueError("transparent_output.duration_pad_s must be >= 0")

    if cfg.transparent_output.codec not in {"qtrle", "png"}:
        raise ValueError("transparent_output.codec must be one of: qtrle, png")

    _validate_hex_color("style.panel_bg_hex", cfg.style.panel_bg_hex)
    _validate_hex_color("style.label_text_hex", cfg.style.label_text_hex)
    _validate_hex_color("style.value_text_hex", cfg.style.value_text_hex)
    _validate_hex_color("style.muted_text_hex", cfg.style.muted_text_hex)

    return cfg


def _validate_hex_color(key: str, value: str) -> None:
    if not re.fullmatch(r"#?[0-9a-fA-F]{6}", value):
        raise ValueError(f"{key} must be a hex color like #1E2434")
