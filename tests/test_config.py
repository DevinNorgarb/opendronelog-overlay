from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from flightframe.config import GaugesConfig, OverlayConfig, load_config
from opendronelog_overlay.config import load_config as load_config_with_components


class TestDefaultConfig:
    def test_default_gauges_disabled(self):
        cfg = load_config(None)
        assert cfg.gauges.enabled is False
        assert cfg.gauges.layout == "horizontal"
        assert cfg.gauges.width == 140
        assert cfg.gauges.height == 140

    def test_default_config_is_complete(self):
        cfg = OverlayConfig()
        assert isinstance(cfg.gauges, GaugesConfig)
        assert cfg.video.width == 260
        assert cfg.style.panel_bg_hex == "#1E2434"


class TestGaugeValidation:
    def _write_config(self, overrides: dict) -> Path:
        base = {
            "video": {"x": 28, "y": 28, "width": 260, "row_height": 30, "opacity": 0.58, "corner_radius": 14},
            "transparent_output": {"width": 1920, "height": 1080, "fps": 30, "codec": "png"},
            "telemetry": {
                "include": ["height", "speed", "battery"],
                "unit_system": "auto",
            },
        }
        merged = {**base}
        for k, v in overrides.items():
            if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
                merged[k] = {**merged[k], **v}
            else:
                merged[k] = v

        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        yaml.dump(merged, tmp)
        tmp.close()
        return Path(tmp.name)

    def test_valid_gauges_enabled(self):
        path = self._write_config({"gauges": {"enabled": True}})
        cfg = load_config(path)
        assert cfg.gauges.enabled is True

    def test_invalid_layout_rejected(self):
        path = self._write_config({"gauges": {"enabled": True, "layout": "diagonal"}})
        with pytest.raises(ValueError, match="gauges.layout"):
            load_config(path)

    def test_valid_layout_horizontal(self):
        path = self._write_config({"gauges": {"enabled": True, "layout": "horizontal"}})
        cfg = load_config(path)
        assert cfg.gauges.layout == "horizontal"

    def test_valid_layout_vertical(self):
        path = self._write_config({"gauges": {"enabled": True, "layout": "vertical"}})
        cfg = load_config(path)
        assert cfg.gauges.layout == "vertical"

    def test_negative_width_rejected(self):
        path = self._write_config({"gauges": {"enabled": True, "width": -10}})
        with pytest.raises(ValueError, match="gauges.width"):
            load_config(path)

    def test_zero_height_rejected(self):
        path = self._write_config({"gauges": {"enabled": True, "height": 0}})
        with pytest.raises(ValueError, match="gauges.height"):
            load_config(path)

    def test_negative_gap_rejected(self):
        path = self._write_config({"gauges": {"enabled": True, "gap": -5}})
        with pytest.raises(ValueError, match="gauges.gap"):
            load_config(path)

    def test_zero_gap_accepted(self):
        path = self._write_config({"gauges": {"enabled": True, "gap": 0}})
        cfg = load_config(path)
        assert cfg.gauges.gap == 0

    def test_invalid_arc_color_rejected(self):
        path = self._write_config({"gauges": {"enabled": True, "arc_color_hex": "notacolor"}})
        with pytest.raises(ValueError, match="gauges.arc_color_hex"):
            load_config(path)

    def test_invalid_needle_color_rejected(self):
        path = self._write_config({"gauges": {"enabled": True, "needle_color_hex": "#XYZ123"}})
        with pytest.raises(ValueError, match="gauges.needle_color_hex"):
            load_config(path)

    def test_gauges_validation_skipped_when_disabled(self):
        path = self._write_config({"gauges": {"enabled": False, "layout": "bad"}})
        cfg = load_config(path)
        assert cfg.gauges.enabled is False


class TestDecimalsValidation:
    def _write_config(self, decimals: dict) -> Path:
        raw = {
            "video": {"x": 28, "y": 28, "width": 260},
            "transparent_output": {"width": 1920, "height": 1080, "fps": 30, "codec": "png"},
            "telemetry": {
                "include": ["height", "speed", "battery"],
                "decimals": decimals,
                "unit_system": "auto",
            },
        }
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        yaml.dump(raw, tmp)
        tmp.close()
        return Path(tmp.name)

    def test_negative_decimal_rejected(self):
        path = self._write_config({"height": -1})
        with pytest.raises(ValueError, match="telemetry.decimals"):
            load_config(path)

    def test_zero_decimal_accepted(self):
        path = self._write_config({"height": 0})
        cfg = load_config(path)
        assert cfg.telemetry.decimals["height"] == 0

    def test_non_integer_decimal_rejected(self):
        path = self._write_config({"height": 1.5})  # yaml loads as float
        with pytest.raises(ValueError, match="telemetry.decimals"):
            load_config(path)


class TestBackwardCompatibility:
    def test_existing_config_without_gauges_section(self):
        raw = {
            "video": {"x": 28, "y": 28, "width": 260},
            "transparent_output": {"width": 1920, "height": 1080, "fps": 30, "codec": "png"},
            "telemetry": {
                "include": ["height", "speed", "battery", "satellites", "lat", "lng", "flight_mode"],
                "unit_system": "auto",
            },
            "rc_sticks": {"enabled": True, "size": 54, "gap": 12},
        }
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        yaml.dump(raw, tmp)
        tmp.close()
        cfg = load_config(Path(tmp.name))
        assert cfg.gauges.enabled is False
        assert cfg.rc_sticks.enabled is True


class TestComponentsSchema:
    def test_components_require_unique_ids(self):
        raw = {
            "video": {"x": 28, "y": 28, "width": 260},
            "transparent_output": {"width": 1920, "height": 1080, "fps": 30, "codec": "png"},
            "telemetry": {"include": ["height", "speed", "battery"], "unit_system": "auto"},
            "components": [
                {"id": "a", "type": "value_card", "rect": {"x": 0, "y": 0, "w": 100, "h": 100}},
                {"id": "a", "type": "value_card", "rect": {"x": 10, "y": 10, "w": 100, "h": 100}},
            ],
        }
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        yaml.dump(raw, tmp)
        tmp.close()
        with pytest.raises(ValueError, match="Duplicate component id"):
            load_config_with_components(Path(tmp.name))

    def test_theme_is_validated(self):
        raw = {
            "video": {"x": 28, "y": 28, "width": 260},
            "transparent_output": {"width": 1920, "height": 1080, "fps": 30, "codec": "png"},
            "telemetry": {"include": ["height", "speed", "battery"], "unit_system": "auto"},
            "theme": {"accent_hex": "not-a-color"},
        }
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        yaml.dump(raw, tmp)
        tmp.close()
        with pytest.raises(ValueError, match="theme.accent_hex"):
            load_config_with_components(Path(tmp.name))
