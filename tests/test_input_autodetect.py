from __future__ import annotations

import csv
import tempfile
import time
from pathlib import Path

from typer.testing import CliRunner

from flightframe.cli import app


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)


class TestCliInputAutodetect:
    def test_srt_accepts_input_csv_directory_and_picks_newest(self):
        tmp_dir = Path(tempfile.mkdtemp())
        older = tmp_dir / "FlightRecord_older.csv"
        newer = tmp_dir / "FlightRecord_newer.csv"

        _write_csv(older, [{"time_s": 0.0, "speed_ms": 1.0}, {"time_s": 1.0, "speed_ms": 1.0}])
        time.sleep(0.01)
        _write_csv(newer, [{"time_s": 0.0, "speed_ms": 9.0}, {"time_s": 1.0, "speed_ms": 9.0}])

        out_srt = tmp_dir / "out.srt"
        runner = CliRunner()
        res = runner.invoke(app, ["srt", "--input-csv", str(tmp_dir), "--output-srt", str(out_srt)])
        assert res.exit_code == 0, res.output
        assert out_srt.exists()

        s = out_srt.read_text(encoding="utf-8")
        # Should reflect the newer file's speed.
        assert "Speed: 9.0" in s


