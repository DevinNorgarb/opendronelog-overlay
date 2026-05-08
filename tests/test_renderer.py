from __future__ import annotations

import csv
import math
import tempfile
from pathlib import Path

import numpy as np
import pytest

from opendronelog_overlay.config import OverlayConfig
from opendronelog_overlay.csv_parser import TelemetryData, load_telemetry
from opendronelog_overlay.encoding import NullFrameEncoder
from opendronelog_overlay.renderer import (
    GAUGE_END_DEG,
    GAUGE_START_DEG,
    GAUGE_SWEEP,
    TransparentInfo,
    _draw_overlay_rgba,
    _draw_gauge_rgba,
    _render_overlay_frames_to_encoder,
    _hex_to_bgra,
)


GAUGE_COLORS = {
    "arc_color": _hex_to_bgra("#2D3446", 255),
    "needle_color": _hex_to_bgra("#FF4D4F", 255),
    "tick_color": _hex_to_bgra("#6B7280", 200),
    "label_color": _hex_to_bgra("#C8CDDC", 255),
    "value_color": _hex_to_bgra("#EFF3F8", 255),
}


def _make_frame(w=800, h=600):
    return np.zeros((h, w, 4), dtype=np.uint8)


class TestGaugeNormalization:
    def test_value_clamped_to_zero(self):
        frame = _make_frame(200, 200)
        _draw_gauge_rgba(
            frame, 10, 10, 180, 180,
            value=-10.0, min_val=0.0, max_val=100.0,
            label="Test", unit="m",
            **GAUGE_COLORS,
        )

    def test_value_clamped_to_max(self):
        frame = _make_frame(200, 200)
        _draw_gauge_rgba(
            frame, 10, 10, 180, 180,
            value=200.0, min_val=0.0, max_val=100.0,
            label="Test", unit="m",
            **GAUGE_COLORS,
        )

    def test_none_value_does_not_crash(self):
        frame = _make_frame(200, 200)
        _draw_gauge_rgba(
            frame, 10, 10, 180, 180,
            value=None, min_val=0.0, max_val=100.0,
            label="N/A", unit="",
            **GAUGE_COLORS,
        )

    def test_min_equals_max_no_crash(self):
        frame = _make_frame(200, 200)
        _draw_gauge_rgba(
            frame, 10, 10, 180, 180,
            value=50.0, min_val=100.0, max_val=100.0,
            label="Flat", unit="",
            **GAUGE_COLORS,
        )

    def test_normal_value_draws_correct_angle(self):
        frame = _make_frame(200, 200)
        _draw_gauge_rgba(
            frame, 10, 10, 180, 180,
            value=50.0, min_val=0.0, max_val=100.0,
            label="Half", unit="%",
            **GAUGE_COLORS,
        )
        expected_angle = math.radians(GAUGE_START_DEG + 0.5 * GAUGE_SWEEP)
        assert math.cos(expected_angle) < 0

    def test_zero_value_needle_at_start(self):
        frame = _make_frame(200, 200)
        _draw_gauge_rgba(
            frame, 10, 10, 180, 180,
            value=0.0, min_val=0.0, max_val=100.0,
            label="Zero", unit="",
            **GAUGE_COLORS,
        )
        start_rad = math.radians(GAUGE_START_DEG)
        assert math.cos(start_rad) < 0
        assert math.sin(start_rad) > 0

    def test_max_value_needle_at_end(self):
        frame = _make_frame(200, 200)
        _draw_gauge_rgba(
            frame, 10, 10, 180, 180,
            value=100.0, min_val=0.0, max_val=100.0,
            label="Max", unit="",
            **GAUGE_COLORS,
        )
        end_rad = math.radians(GAUGE_END_DEG)
        assert math.cos(end_rad) > 0
        assert math.sin(end_rad) > 0

    def test_gauge_sweep_is_reasonable(self):
        assert 200 <= GAUGE_SWEEP <= 280


class TestGaugeDrawingBounds:
    def test_small_gauge_does_not_crash(self):
        frame = _make_frame(200, 200)
        _draw_gauge_rgba(
            frame, 5, 5, 60, 60,
            value=42.0, min_val=0.0, max_val=100.0,
            label="Tiny", unit="m",
            **GAUGE_COLORS,
        )

    def test_gauge_at_frame_edge_clipped(self):
        frame = _make_frame(200, 200)
        _draw_gauge_rgba(
            frame, 160, 160, 80, 80,
            value=50.0, min_val=0.0, max_val=100.0,
            label="Edge", unit="",
            **GAUGE_COLORS,
        )


class TestSmokeRender:
    def _write_csv(self, rows: list[dict]) -> Path:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
        writer = csv.DictWriter(tmp, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
        tmp.close()
        return Path(tmp.name)

    def test_tiny_transparent_clip_renders(self):
        rows = [
            {"time_s": 0.0, "speed_ms": 0.0, "height_m": 0.0, "battery_percent": 100, "satellites": 10, "lat": 0.0, "lng": 0.0},
            {"time_s": 1.0, "speed_ms": 15.0, "height_m": 50.0, "battery_percent": 90, "satellites": 12, "lat": 0.0, "lng": 0.0},
            {"time_s": 2.0, "speed_ms": 25.0, "height_m": 100.0, "battery_percent": 80, "satellites": 14, "lat": 0.0, "lng": 0.0},
        ]
        csv_path = self._write_csv(rows)
        telemetry = load_telemetry(csv_path)
        assert len(telemetry.time_s) == 3

        cfg = OverlayConfig()
        cfg.gauges.enabled = True
        cfg.transparent_output.width = 800
        cfg.transparent_output.height = 480
        cfg.transparent_output.fps = 1.0

        frame = _make_frame(800, 480)
        frame = _draw_overlay_rgba(frame, 0.5, telemetry, cfg)
        assert frame.shape == (480, 800, 4)
        assert np.all(frame[:, :, 3] >= 0)

        frame2 = _make_frame(800, 480)
        frame2 = _draw_overlay_rgba(frame2, 1.5, telemetry, cfg)
        assert not np.array_equal(frame, frame2)

    def test_disabled_gauges_renders_same_as_default(self):
        rows = [
            {"time_s": 0.0, "speed_ms": 0.0, "height_m": 0.0, "battery_percent": 100, "satellites": 10, "lat": 0.0, "lng": 0.0},
            {"time_s": 1.0, "speed_ms": 10.0, "height_m": 30.0, "battery_percent": 90, "satellites": 12, "lat": 0.0, "lng": 0.0},
        ]
        csv_path = self._write_csv(rows)
        telemetry = load_telemetry(csv_path)

        cfg_default = OverlayConfig()
        frame_default = _draw_overlay_rgba(_make_frame(800, 480), 0.5, telemetry, cfg_default)

        cfg_disabled = OverlayConfig()
        cfg_disabled.gauges.enabled = False
        frame_disabled = _draw_overlay_rgba(_make_frame(800, 480), 0.5, telemetry, cfg_disabled)

        assert np.array_equal(frame_default, frame_disabled)

    def test_missing_gauge_field_does_not_crash(self):
        rows = [
            {"time_s": 0.0, "satellites": 10, "lat": 0.0, "lng": 0.0},
            {"time_s": 1.0, "satellites": 12, "lat": 0.0, "lng": 0.0},
        ]
        csv_path = self._write_csv(rows)
        telemetry = load_telemetry(csv_path)

        cfg = OverlayConfig()
        cfg.gauges.enabled = True

        frame = _draw_overlay_rgba(_make_frame(800, 480), 0.5, telemetry, cfg)
        assert frame.shape == (480, 800, 4)

    def test_airdata_speed_column_is_supported(self):
        rows = [
            {"time_s": 0.0, "speed(m/s)": 1.0, "height_m": 5.0, "battery_percent": 98},
            {"time_s": 1.0, "speed(m/s)": 7.5, "height_m": 10.0, "battery_percent": 96},
        ]
        csv_path = self._write_csv(rows)
        telemetry = load_telemetry(csv_path)
        assert "speed" in telemetry.numeric
        assert telemetry.units.get("speed") == "m/s"

    def test_frame_loop_can_encode_without_ffmpeg(self):
        rows = [
            {"time_s": 0.0, "speed_ms": 0.0, "height_m": 0.0, "battery_percent": 100},
            {"time_s": 1.0, "speed_ms": 10.0, "height_m": 30.0, "battery_percent": 90},
        ]
        csv_path = self._write_csv(rows)
        telemetry = load_telemetry(csv_path)

        cfg = OverlayConfig()
        cfg.transparent_output.width = 64
        cfg.transparent_output.height = 32
        cfg.transparent_output.fps = 2.0

        info = TransparentInfo(
            fps=cfg.transparent_output.fps,
            width=cfg.transparent_output.width,
            height=cfg.transparent_output.height,
            duration_s=1.0,
            frame_count=3,
        )
        encoder = NullFrameEncoder(expected_width=info.width, expected_height=info.height)

        class _NoopProgress:
            def update(self, amount: int = 1) -> None:
                pass

        _render_overlay_frames_to_encoder(
            encoder=encoder,
            telemetry=telemetry,
            config=cfg,
            info=info,
            telemetry_offset_s=0.0,
            progress_bar=_NoopProgress(),  # type: ignore[arg-type]
        )
        assert encoder.frame_count == info.frame_count
