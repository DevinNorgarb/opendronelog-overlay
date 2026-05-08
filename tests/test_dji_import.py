from __future__ import annotations

import csv
import tempfile
from pathlib import Path

import pytest

from opendronelog_overlay.dji_import import _map_djirecord_csv_to_odl_csv


def _write_csv(headers: list[str], rows: list[dict[str, str]]) -> Path:
    p = Path(tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False).name)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return p


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class TestDjiImportMapping:
    def test_maps_ms_time_to_time_s(self):
        raw = _write_csv(
            ["time(millisecond)", "latitude", "longitude", "speed_mph", "battery_percent"],
            [
                {"time(millisecond)": "1000", "latitude": "1", "longitude": "2", "speed_mph": "10", "battery_percent": "90"},
                {"time(millisecond)": "2000", "latitude": "1", "longitude": "2", "speed_mph": "20", "battery_percent": "80"},
            ],
        )
        out = Path(tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name)
        _map_djirecord_csv_to_odl_csv(raw_csv=raw, output_csv=out)
        rows = _read_rows(out)
        assert rows[0]["time_s"] == "0"
        assert rows[1]["time_s"] == "1"

    def test_converts_speed_mph_to_speed_ms(self):
        raw = _write_csv(
            ["time(millisecond)", "latitude", "longitude", "speed_mph"],
            [
                {"time(millisecond)": "0", "latitude": "1", "longitude": "2", "speed_mph": "10"},
                {"time(millisecond)": "1000", "latitude": "1", "longitude": "2", "speed_mph": "10"},
            ],
        )
        out = Path(tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name)
        _map_djirecord_csv_to_odl_csv(raw_csv=raw, output_csv=out)
        rows = _read_rows(out)
        assert rows[0]["speed_ms"].startswith("4.470")  # 10 mph in m/s

    def test_raises_on_missing_time_column(self):
        raw = _write_csv(["latitude", "longitude"], [{"latitude": "1", "longitude": "2"}])
        out = Path(tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name)
        with pytest.raises(ValueError, match="time column"):
            _map_djirecord_csv_to_odl_csv(raw_csv=raw, output_csv=out)

