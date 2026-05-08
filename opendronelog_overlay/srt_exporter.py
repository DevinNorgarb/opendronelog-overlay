from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from .config import OverlayConfig
from .csv_parser import TelemetryData


@dataclass
class _Cue:
    start_s: float
    end_s: float
    text: str


def export_srt(
    output_srt_path: str | Path,
    telemetry: TelemetryData,
    config: OverlayConfig,
    telemetry_offset_s: float = 0.0,
    interval_s: float = 1.0,
) -> int:
    if interval_s <= 0:
        raise ValueError("interval_s must be > 0")

    duration_s = float(telemetry.time_s[-1]) if len(telemetry.time_s) else 0.0
    if duration_s <= 0:
        Path(output_srt_path).write_text("", encoding="utf-8")
        return 0

    steps = max(1, int(np.ceil(duration_s / interval_s)))

    cues: list[_Cue] = []
    pending_text = ""
    pending_start = 0.0

    for idx in range(steps):
        start_s = idx * interval_s
        end_s = min(duration_s, (idx + 1) * interval_s)
        sample_t_video = min(duration_s, start_s + interval_s * 0.5)
        sample_t = sample_t_video - telemetry_offset_s

        text = _telemetry_text_block(sample_t, telemetry, config)

        if idx == 0:
            pending_text = text
            pending_start = start_s
            continue

        if text != pending_text:
            if pending_text:
                cues.append(_Cue(start_s=pending_start, end_s=start_s, text=pending_text))
            pending_text = text
            pending_start = start_s

    if pending_text:
        cues.append(_Cue(start_s=pending_start, end_s=duration_s, text=pending_text))

    srt = _serialize_cues(cues)
    Path(output_srt_path).write_text(srt, encoding="utf-8")
    return len(cues)


def _telemetry_text_block(t: float, telemetry: TelemetryData, config: OverlayConfig) -> str:
    lines: list[str] = []
    for field in config.telemetry.include:
        line = _format_field_line(field, t, telemetry, config)
        if line is None:
            continue
        label, value = line
        lines.append(f"{label}: {value}")
    return "\n".join(lines)


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


def _serialize_cues(cues: Iterable[_Cue]) -> str:
    chunks: list[str] = []
    for idx, cue in enumerate(cues, start=1):
        chunks.append(str(idx))
        chunks.append(f"{_fmt_srt_time(cue.start_s)} --> {_fmt_srt_time(cue.end_s)}")
        chunks.append(cue.text)
        chunks.append("")
    return "\n".join(chunks)


def _fmt_srt_time(seconds: float) -> str:
    ms_total = max(0, int(round(seconds * 1000.0)))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
