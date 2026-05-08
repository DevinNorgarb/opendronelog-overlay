from __future__ import annotations

import csv
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class DjiImportResult:
    raw_csv_path: Path
    odl_csv_path: Path


def convert_dji_txt_to_odl_csv_via_djirecord(
    *,
    input_txt: Path,
    output_csv: Path,
    api_key: str | None = None,
    no_verify: bool = False,
) -> DjiImportResult:
    """
    Convert a DJI FlightRecord .txt (binary) to an OpenDroneLog-style CSV that this project can ingest.

    Implementation strategy: call the external `djirecord` CLI (from pydjirecord) to decode
    the binary format, then map its CSV output into our expected columns (`time_s`, etc.).

    This keeps protobuf/encryption dependencies out of this package's runtime.
    """
    if not input_txt.exists():
        raise FileNotFoundError(str(input_txt))
    if shutil.which("djirecord") is None:
        raise RuntimeError(
            "Missing `djirecord` executable. Install pydjirecord (recommended via pipx):\n"
            "  brew install pipx && pipx ensurepath\n"
            "  pipx install pydjirecord\n"
        )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="odl-dji-import-"))
    raw_csv = tmp_dir / "djirecord.csv"

    cmd: list[str] = ["djirecord", str(input_txt), "--csv", "-o", str(raw_csv)]
    if api_key:
        cmd += ["--api-key", api_key]
    if no_verify:
        cmd += ["--no-verify"]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        msg = stderr or stdout or "djirecord failed"
        raise RuntimeError(msg)
    if not raw_csv.exists() or raw_csv.stat().st_size == 0:
        raise RuntimeError("djirecord produced no CSV output")

    _map_djirecord_csv_to_odl_csv(raw_csv=raw_csv, output_csv=output_csv)
    return DjiImportResult(raw_csv_path=raw_csv, odl_csv_path=output_csv)


def _map_djirecord_csv_to_odl_csv(*, raw_csv: Path, output_csv: Path) -> None:
    rows = list(_read_csv_dicts(raw_csv))
    if not rows:
        raise ValueError("Empty djirecord CSV")

    headers = list(rows[0].keys())

    time_key, time_unit = _pick_time_key(headers)
    field_map = _build_field_map(headers)

    # Normalize time to start at 0.
    times_s: list[float] = []
    for r in rows:
        v = _parse_float(r.get(time_key))
        if v is None:
            continue
        if time_unit == "ms":
            v = v / 1000.0
        times_s.append(v)
    if len(times_s) < 2:
        raise ValueError(f"Not enough usable time samples in djirecord CSV (time key: {time_key!r})")

    t0 = times_s[0]

    out_headers = [
        "time_s",
        "lat",
        "lng",
        "height_m",
        "altitude_m",
        "speed_ms",
        "battery_percent",
        "satellites",
        "flight_mode",
        "rc_aileron",
        "rc_elevator",
        "rc_throttle",
        "rc_rudder",
    ]

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=out_headers)
        w.writeheader()

        for r in rows:
            t = _parse_float(r.get(time_key))
            if t is None:
                continue
            if time_unit == "ms":
                t = t / 1000.0
            t = max(0.0, t - t0)

            out: dict[str, str] = {"time_s": f"{t:.6f}".rstrip("0").rstrip(".")}

            # Map numeric fields.
            out["lat"] = _fmt_float(_parse_float(r.get(field_map.get("lat", ""))))
            out["lng"] = _fmt_float(_parse_float(r.get(field_map.get("lng", ""))))
            out["height_m"] = _fmt_float(_parse_float(r.get(field_map.get("height_m", ""))))
            out["altitude_m"] = _fmt_float(_parse_float(r.get(field_map.get("altitude_m", ""))))

            # Speed: prefer m/s fields; if only mph/kmh exist, convert.
            speed_val, speed_unit = _extract_speed_value(r, headers)
            if speed_val is None:
                out["speed_ms"] = ""
            else:
                if speed_unit == "mph":
                    speed_val = speed_val * 0.44704
                elif speed_unit == "kmh":
                    speed_val = speed_val * (1000.0 / 3600.0)
                out["speed_ms"] = _fmt_float(speed_val)

            out["battery_percent"] = _fmt_int(_parse_float(r.get(field_map.get("battery_percent", ""))))
            out["satellites"] = _fmt_int(_parse_float(r.get(field_map.get("satellites", ""))))
            out["flight_mode"] = (r.get(field_map.get("flight_mode", "")) or "").strip()

            # RC sticks are often present as raw or percent; we pass through numbers if available.
            for k in ["rc_aileron", "rc_elevator", "rc_throttle", "rc_rudder"]:
                out[k] = _fmt_float(_parse_float(r.get(field_map.get(k, ""))))

            w.writerow(out)


def _read_csv_dicts(path: Path) -> Iterable[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield {k: (v or "") for k, v in row.items()}


def _parse_float(v: str | None) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _fmt_float(v: float | None) -> str:
    if v is None:
        return ""
    if not (v == v and abs(v) != float("inf")):
        return ""
    return f"{v:.6f}".rstrip("0").rstrip(".")


def _fmt_int(v: float | None) -> str:
    if v is None:
        return ""
    try:
        return str(int(round(v)))
    except Exception:
        return ""


def _pick_time_key(headers: list[str]) -> tuple[str, str]:
    """
    Returns (column_name, unit) where unit is 's' or 'ms'.
    """
    lower_to_orig = {h.strip().lower(): h for h in headers}

    def has(col: str) -> bool:
        return col.strip().lower() in lower_to_orig

    def orig(col: str) -> str:
        return lower_to_orig[col.strip().lower()]

    candidates_ms = [
        "time(millisecond)",
        "time_ms",
        "timestamp_ms",
        "time_millis",
    ]
    for c in candidates_ms:
        if has(c):
            return orig(c), "ms"

    candidates_s = [
        "time_s",
        "fly_time",
        "time",
        "timestamp_s",
        "osd.flytime",
    ]
    for c in candidates_s:
        if has(c):
            return orig(c), "s"

    raise ValueError(f"Could not find a time column in djirecord CSV headers: {headers[:40]}")


def _first_existing(headers: list[str], candidates: list[str]) -> str | None:
    lower_to_orig = {h.strip().lower(): h for h in headers}
    for c in candidates:
        key = c.strip().lower()
        if key in lower_to_orig:
            return lower_to_orig[key]
    return None


def _build_field_map(headers: list[str]) -> dict[str, str]:
    m: dict[str, str] = {}

    lat = _first_existing(headers, ["lat", "latitude", "osd.latitude"])
    lng = _first_existing(headers, ["lng", "lon", "longitude", "osd.longitude"])
    if lat:
        m["lat"] = lat
    if lng:
        m["lng"] = lng

    m_h = _first_existing(headers, ["height_m", "height", "osd.height", "ultrasonicheight", "osd.vpsheight"])
    if m_h:
        m["height_m"] = m_h

    alt = _first_existing(headers, ["altitude_m", "altitude", "osd.altitude"])
    if alt:
        m["altitude_m"] = alt

    batt = _first_existing(headers, ["battery_percent", "battery", "battery.charge_level", "charge_level"])
    if batt:
        m["battery_percent"] = batt

    sats = _first_existing(headers, ["satellites", "gps_num", "osd.gps_num", "osd.gpsnum"])
    if sats:
        m["satellites"] = sats

    mode = _first_existing(headers, ["flight_mode", "flycstate", "flycstateraw", "osd.flyc_state", "osd.flycstate"])
    if mode:
        m["flight_mode"] = mode

    for k, cands in {
        "rc_aileron": ["rc_aileron", "rc.aileron"],
        "rc_elevator": ["rc_elevator", "rc.elevator"],
        "rc_throttle": ["rc_throttle", "rc.throttle"],
        "rc_rudder": ["rc_rudder", "rc.rudder"],
    }.items():
        found = _first_existing(headers, cands)
        if found:
            m[k] = found

    return m


def _extract_speed_value(row: dict[str, str], headers: list[str]) -> tuple[float | None, str]:
    lower_to_orig = {h.strip().lower(): h for h in headers}

    def has(col: str) -> bool:
        return col.strip().lower() in lower_to_orig

    def get(col: str) -> str | None:
        k = col.strip().lower()
        if k not in lower_to_orig:
            return None
        return row.get(lower_to_orig[k])

    # Prefer explicit m/s columns if present.
    for key in [
        "speed_ms",
        "speed(m/s)",
        "speed",
        "osd.speed",
        "osd.hspeed",
        "osd.xspeed",
        "osd.yspeed",
        "osd.zspeed",
    ]:
        if has(key):
            v = _parse_float(get(key))
            if v is not None:
                return v, "ms"

    mph = _first_existing(headers, ["speed_mph", "speed(mph)"])
    if mph:
        v = _parse_float(row.get(mph))
        if v is not None:
            return v, "mph"

    kmh = _first_existing(headers, ["speed_kmh", "speed(km/h)"])
    if kmh:
        v = _parse_float(row.get(kmh))
        if v is not None:
            return v, "kmh"

    return None, "ms"

