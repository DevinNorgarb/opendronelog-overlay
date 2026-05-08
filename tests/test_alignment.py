from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from typer.testing import CliRunner

from opendronelog_overlay.cli import app
from opendronelog_overlay.config import OverlayConfig
from opendronelog_overlay.csv_parser import load_telemetry
from opendronelog_overlay.srt_exporter import export_srt


def _write_csv(rows: list[dict]) -> Path:
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
    writer = csv.DictWriter(tmp, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    tmp.close()
    return Path(tmp.name)


def _read_srt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestSrtAlignment:
    def test_srt_alignment_changes_content_with_offset(self):
        rows = [
            {"time_s": 0.0, "speed_ms": 0.0},
            {"time_s": 1.0, "speed_ms": 10.0},
            {"time_s": 2.0, "speed_ms": 20.0},
        ]
        csv_path = _write_csv(rows)
        cfg = OverlayConfig()
        cfg.telemetry.include = ["speed"]
        telemetry = load_telemetry(csv_path, unit_system=cfg.telemetry.unit_system)

        out0 = Path(tempfile.NamedTemporaryFile(suffix=".srt", delete=False).name)
        out1 = Path(tempfile.NamedTemporaryFile(suffix=".srt", delete=False).name)

        export_srt(out0, telemetry, cfg, telemetry_offset_s=0.0, interval_s=1.0)
        export_srt(out1, telemetry, cfg, telemetry_offset_s=0.5, interval_s=1.0)

        assert _read_srt(out0) != _read_srt(out1)

    def test_offset_sign_convention_samples_earlier_with_positive_offset(self):
        rows = [
            {"time_s": 0.0, "speed_ms": 0.0},
            {"time_s": 1.0, "speed_ms": 10.0},
        ]
        csv_path = _write_csv(rows)
        cfg = OverlayConfig()
        cfg.telemetry.include = ["speed"]
        telemetry = load_telemetry(csv_path, unit_system=cfg.telemetry.unit_system)

        out0 = Path(tempfile.NamedTemporaryFile(suffix=".srt", delete=False).name)
        out_pos = Path(tempfile.NamedTemporaryFile(suffix=".srt", delete=False).name)

        export_srt(out0, telemetry, cfg, telemetry_offset_s=0.0, interval_s=1.0)
        export_srt(out_pos, telemetry, cfg, telemetry_offset_s=1.0, interval_s=1.0)

        s0 = _read_srt(out0)
        s1 = _read_srt(out_pos)

        # At 0.5s sample time, offset=0 produces interpolated 5.0 m/s.
        assert "Speed: 5.0 m/s" in s0
        # Positive offset samples earlier; at -0.5s it clamps to the first value (0.0).
        assert "Speed: 0.0 m/s" in s1

    def test_negative_sample_times_do_not_crash(self):
        rows = [
            {"time_s": 0.0, "speed_ms": 3.0},
            {"time_s": 1.0, "speed_ms": 3.0},
        ]
        csv_path = _write_csv(rows)
        cfg = OverlayConfig()
        cfg.telemetry.include = ["speed"]
        telemetry = load_telemetry(csv_path, unit_system=cfg.telemetry.unit_system)

        out = Path(tempfile.NamedTemporaryFile(suffix=".srt", delete=False).name)
        export_srt(out, telemetry, cfg, telemetry_offset_s=10.0, interval_s=1.0)
        assert out.exists()
        assert "Speed: 3.0 m/s" in _read_srt(out)


class TestCliPlumbing:
    def test_cli_srt_command_threads_telemetry_offset(self):
        rows = [
            {"time_s": 0.0, "speed_ms": 0.0},
            {"time_s": 1.0, "speed_ms": 10.0},
        ]
        csv_path = _write_csv(rows)

        cfg = OverlayConfig()
        cfg.telemetry.include = ["speed"]

        out_dir = Path(tempfile.mkdtemp())
        cfg_path = out_dir / "cfg.yaml"
        cfg_path.write_text(
            "\n".join(
                [
                    "telemetry:",
                    "  include: [speed]",
                    "transparent_output:",
                    "  width: 64",
                    "  height: 32",
                    "  fps: 1",
                    "  codec: png",
                ]
            ),
            encoding="utf-8",
        )

        out0 = out_dir / "a.srt"
        out1 = out_dir / "b.srt"

        runner = CliRunner()
        r0 = runner.invoke(
            app,
            [
                "srt",
                "--input-csv",
                str(csv_path),
                "--config",
                str(cfg_path),
                "--output-srt",
                str(out0),
                "--telemetry-offset-s",
                "0",
            ],
        )
        assert r0.exit_code == 0, r0.output

        r1 = runner.invoke(
            app,
            [
                "srt",
                "--input-csv",
                str(csv_path),
                "--config",
                str(cfg_path),
                "--output-srt",
                str(out1),
                "--telemetry-offset-s",
                "1",
            ],
        )
        assert r1.exit_code == 0, r1.output

        assert _read_srt(out0) != _read_srt(out1)

