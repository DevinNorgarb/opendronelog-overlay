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
class ThemeConfig:
    """
    Global defaults for component styling. Components may override.
    """

    panel_bg_hex: str = "#1E2434"
    label_text_hex: str = "#C8CDDC"
    value_text_hex: str = "#EFF3F8"
    muted_text_hex: str = "#AAB2C2"

    accent_hex: str = "#FF4D4F"
    arc_hex: str = "#2D3446"
    tick_hex: str = "#6B7280"


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
class GaugesConfig:
    enabled: bool = False
    layout: str = "horizontal"
    width: int = 140
    height: int = 140
    x: int = -1
    y: int = 28
    gap: int = 14
    arc_color_hex: str = "#2D3446"
    needle_color_hex: str = "#FF4D4F"
    tick_color_hex: str = "#6B7280"
    label_color_hex: str = "#C8CDDC"
    value_color_hex: str = "#EFF3F8"


@dataclass
class ComponentRect:
    x: int
    y: int
    w: int
    h: int


@dataclass
class OverlayComponent:
    id: str
    type: str
    rect: ComponentRect
    config: dict[str, Any] = field(default_factory=dict)
    style: dict[str, Any] = field(default_factory=dict)


@dataclass
class OverlayConfig:
    video: VideoPanelConfig = field(default_factory=VideoPanelConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    rc_sticks: RcSticksConfig = field(default_factory=RcSticksConfig)
    transparent_output: TransparentOutputConfig = field(default_factory=TransparentOutputConfig)
    style: StyleConfig = field(default_factory=StyleConfig)
    gauges: GaugesConfig = field(default_factory=GaugesConfig)
    theme: ThemeConfig = field(default_factory=ThemeConfig)
    components: list[OverlayComponent] = field(default_factory=list)


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
    "heading_deg",
    "gimbal_heading_deg",
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
            "gauges": default_cfg.gauges.__dict__,
            "theme": default_cfg.theme.__dict__,
            "components": [],
        },
        raw,
    )

    components = _parse_components(merged.get("components") or [])

    cfg = OverlayConfig(
        video=VideoPanelConfig(**merged["video"]),
        telemetry=TelemetryConfig(**merged["telemetry"]),
        rc_sticks=RcSticksConfig(**merged["rc_sticks"]),
        transparent_output=TransparentOutputConfig(**merged["transparent_output"]),
        style=StyleConfig(**merged["style"]),
        gauges=GaugesConfig(**merged["gauges"]),
        theme=ThemeConfig(**merged["theme"]),
        components=components,
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

    _validate_hex_color("theme.panel_bg_hex", cfg.theme.panel_bg_hex)
    _validate_hex_color("theme.label_text_hex", cfg.theme.label_text_hex)
    _validate_hex_color("theme.value_text_hex", cfg.theme.value_text_hex)
    _validate_hex_color("theme.muted_text_hex", cfg.theme.muted_text_hex)
    _validate_hex_color("theme.accent_hex", cfg.theme.accent_hex)
    _validate_hex_color("theme.arc_hex", cfg.theme.arc_hex)
    _validate_hex_color("theme.tick_hex", cfg.theme.tick_hex)

    for field_key, d in cfg.telemetry.decimals.items():
        if not isinstance(d, int) or d < 0:
            raise ValueError(f"telemetry.decimals[{field_key}] must be a non-negative integer, got {d!r}")

    if cfg.gauges.enabled:
        if cfg.gauges.layout not in {"horizontal", "vertical"}:
            raise ValueError("gauges.layout must be one of: horizontal, vertical")
        if cfg.gauges.width <= 0 or cfg.gauges.height <= 0:
            raise ValueError("gauges.width and gauges.height must be > 0")
        if cfg.gauges.gap < 0:
            raise ValueError("gauges.gap must be >= 0")
        _validate_hex_color("gauges.arc_color_hex", cfg.gauges.arc_color_hex)
        _validate_hex_color("gauges.needle_color_hex", cfg.gauges.needle_color_hex)
        _validate_hex_color("gauges.tick_color_hex", cfg.gauges.tick_color_hex)
        _validate_hex_color("gauges.label_color_hex", cfg.gauges.label_color_hex)
        _validate_hex_color("gauges.value_color_hex", cfg.gauges.value_color_hex)

    return cfg


def _validate_hex_color(key: str, value: str) -> None:
    if not re.fullmatch(r"#?[0-9a-fA-F]{6}", value):
        raise ValueError(f"{key} must be a hex color like #1E2434")


def _parse_components(raw_components: list[Any]) -> list[OverlayComponent]:
    if not raw_components:
        return []
    if not isinstance(raw_components, list):
        raise ValueError("components must be a list")

    seen: set[str] = set()
    out: list[OverlayComponent] = []
    for idx, item in enumerate(raw_components):
        if not isinstance(item, dict):
            raise ValueError(f"components[{idx}] must be a mapping")

        comp_id = item.get("id")
        if not isinstance(comp_id, str) or not comp_id.strip():
            raise ValueError(f"components[{idx}].id must be a non-empty string")
        if comp_id in seen:
            raise ValueError(f"Duplicate component id: {comp_id}")
        seen.add(comp_id)

        comp_type = item.get("type")
        if not isinstance(comp_type, str) or not comp_type.strip():
            raise ValueError(f"components[{idx}].type must be a non-empty string")

        rect = item.get("rect") or {}
        if not isinstance(rect, dict):
            raise ValueError(f"components[{idx}].rect must be a mapping")
        try:
            r = ComponentRect(
                x=int(rect.get("x", 0)),
                y=int(rect.get("y", 0)),
                w=int(rect.get("w", 100)),
                h=int(rect.get("h", 100)),
            )
        except Exception as e:
            raise ValueError(f"components[{idx}].rect must have integer x/y/w/h") from e
        if r.w <= 0 or r.h <= 0:
            raise ValueError(f"components[{idx}].rect.w and rect.h must be > 0")

        cfg = item.get("config") or {}
        style = item.get("style") or {}
        if not isinstance(cfg, dict):
            raise ValueError(f"components[{idx}].config must be a mapping")
        if not isinstance(style, dict):
            raise ValueError(f"components[{idx}].style must be a mapping")

        out.append(OverlayComponent(id=comp_id, type=comp_type, rect=r, config=cfg, style=style))
    return out


def config_to_raw_yaml_dict(cfg: OverlayConfig) -> dict[str, Any]:
    return {
        "video": cfg.video.__dict__,
        "style": cfg.style.__dict__,
        "theme": cfg.theme.__dict__,
        "transparent_output": cfg.transparent_output.__dict__,
        "telemetry": {
            "include": list(cfg.telemetry.include),
            "labels": dict(cfg.telemetry.labels),
            "decimals": dict(cfg.telemetry.decimals),
            "unit_system": cfg.telemetry.unit_system,
        },
        "rc_sticks": cfg.rc_sticks.__dict__,
        "gauges": cfg.gauges.__dict__,
        "components": [
            {
                "id": c.id,
                "type": c.type,
                "rect": {"x": c.rect.x, "y": c.rect.y, "w": c.rect.w, "h": c.rect.h},
                "config": dict(c.config),
                "style": dict(c.style),
            }
            for c in cfg.components
        ],
    }


def dump_config_yaml(cfg: OverlayConfig) -> str:
    return yaml.safe_dump(config_to_raw_yaml_dict(cfg), sort_keys=False, allow_unicode=True)
