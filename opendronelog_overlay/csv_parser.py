from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl


@dataclass
class TelemetryData:
    time_s: np.ndarray
    numeric: dict[str, np.ndarray]
    text: dict[str, list[str]]
    units: dict[str, str]


NUMERIC_FIELD_ALIASES = {
    "height": ["height_m", "height_ft"],
    "distance_to_home": ["distance_to_home_m", "distance_to_home_ft"],
    "altitude": ["altitude_m", "altitude_ft"],
    "speed": [
        "speed_ms",
        "speed_mph",
        "speed_kmh",
        "speed(m/s)",
        "speed(mph)",
        "speed(km/h)",
    ],
    "battery": ["battery_percent"],
    "battery_voltage": ["battery_voltage_v"],
    "battery_temp": ["battery_temp_c", "battery_temp_f"],
    "satellites": ["satellites"],
    "lat": ["lat"],
    "lng": ["lng"],
    "rc_aileron": ["rc_aileron"],
    "rc_elevator": ["rc_elevator"],
    "rc_throttle": ["rc_throttle"],
    "rc_rudder": ["rc_rudder"],
}

TEXT_FIELD_ALIASES = {
    "flight_mode": ["flight_mode"],
}


def _pick_first_existing(columns: list[str], aliases: list[str]) -> str | None:
    for name in aliases:
        if name in columns:
            return name
    return None


def _extract_unit(column_name: str) -> str:
    name = column_name.strip().lower()
    if "(m/s)" in name:
        return "m/s"
    if "(mph)" in name:
        return "mph"
    if "(km/h)" in name:
        return "km/h"
    if column_name.endswith("_m"):
        return "m"
    if column_name.endswith("_ft"):
        return "ft"
    if column_name.endswith("_ms"):
        return "m/s"
    if column_name.endswith("_mph"):
        return "mph"
    if column_name.endswith("_kmh"):
        return "km/h"
    if column_name.endswith("_c"):
        return "C"
    if column_name.endswith("_f"):
        return "F"
    if column_name.endswith("_v"):
        return "V"
    if column_name.endswith("_percent"):
        return "%"
    return ""


def _to_numeric(df: pl.DataFrame, column: str) -> np.ndarray:
    return (
        df.select(pl.col(column).cast(pl.Float64, strict=False))
        .to_series()
        .fill_null(strategy="forward")
        .fill_null(strategy="backward")
        .fill_null(0.0)
        .to_numpy()
    )


def load_telemetry(csv_path: str | Path, unit_system: str = "auto") -> TelemetryData:
    df = pl.read_csv(
        csv_path,
        infer_schema_length=1000,
        truncate_ragged_lines=True,
        ignore_errors=True,
    )

    if "time_s" not in df.columns:
        raise ValueError("CSV is missing required column: time_s")

    time_s = _to_numeric(df, "time_s")
    if len(time_s) < 2:
        raise ValueError("Telemetry CSV must contain at least 2 rows")

    numeric: dict[str, np.ndarray] = {}
    text: dict[str, list[str]] = {}
    units: dict[str, str] = {}

    for canonical, aliases in NUMERIC_FIELD_ALIASES.items():
        source = _pick_first_existing(df.columns, aliases)
        if source is None:
            continue
        values = _to_numeric(df, source)
        source_unit = _extract_unit(source)
        values, source_unit = _convert_units_if_needed(canonical, values, source_unit, unit_system)
        numeric[canonical] = values
        units[canonical] = source_unit

    for canonical, aliases in TEXT_FIELD_ALIASES.items():
        source = _pick_first_existing(df.columns, aliases)
        if source is None:
            continue
        text[canonical] = (
            df.select(pl.col(source).cast(pl.String, strict=False))
            .to_series()
            .fill_null("")
            .to_list()
        )

    return TelemetryData(time_s=time_s, numeric=numeric, text=text, units=units)


def _convert_units_if_needed(
    field: str,
    values: np.ndarray,
    source_unit: str,
    unit_system: str,
) -> tuple[np.ndarray, str]:
    if unit_system == "auto":
        return values, source_unit

    if field in {"height", "distance_to_home", "altitude"}:
        if source_unit == "ft" and unit_system == "metric":
            return values * 0.3048, "m"
        if source_unit == "m" and unit_system == "imperial":
            return values * 3.28084, "ft"

    if field == "speed":
        if source_unit == "mph" and unit_system == "metric":
            return values * 0.44704, "m/s"
        if source_unit == "m/s" and unit_system == "imperial":
            return values * 2.23694, "mph"
        if source_unit == "km/h" and unit_system == "metric":
            return values * (1000.0 / 3600.0), "m/s"
        if source_unit == "km/h" and unit_system == "imperial":
            return values * 0.621371, "mph"

    if field == "battery_temp":
        if source_unit == "F" and unit_system == "metric":
            return (values - 32.0) * (5.0 / 9.0), "C"
        if source_unit == "C" and unit_system == "imperial":
            return values * (9.0 / 5.0) + 32.0, "F"

    return values, source_unit
