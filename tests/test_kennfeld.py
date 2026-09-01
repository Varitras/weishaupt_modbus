"""The power map: interpolation on the compiled grid, and the shipped grids."""

import json
import pathlib
from types import SimpleNamespace

import pytest

from custom_components.weishaupt_modbus.const import CONST
from custom_components.weishaupt_modbus.kennfeld import PowerMap, get_filepath

KENNFELD = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "weishaupt_modbus"
    / "kennfeld"
)


def _power_map():
    power_map = PowerMap(SimpleNamespace(data={}), None)
    power_map._known_t = [35, 55]
    power_map._out_range_raw = [-100, 0]
    # Outside temperature (raw, 0.1 °C) -> heating power at 35 °C and 55 °C flow.
    power_map._compiled_grid = {"-100": [5000.0, 4000.0], "0": [6000.0, 5000.0]}
    return power_map


def test_a_grid_point_is_returned_as_is():
    assert _power_map().map(0, 350) == 6000.0
    assert _power_map().map(0, 550) == 5000.0


def test_interpolates_between_flow_curves():
    assert _power_map().map(0, 450) == pytest.approx(5500.0)


def test_flow_temperature_is_clamped_to_the_known_curves():
    assert _power_map().map(0, 800) == 5000.0
    assert _power_map().map(0, 100) == 6000.0


def test_outside_temperature_is_clamped_to_the_grid():
    assert _power_map().map(500, 350) == 6000.0
    assert _power_map().map(-999, 350) == 5000.0


def test_an_empty_grid_reads_as_zero():
    power_map = _power_map()
    power_map._compiled_grid = {}

    assert power_map.map(0, 350) == 0.0


def test_the_fallback_path_is_the_shipped_grid_folder():
    """Without a kennfeld folder in the config directory (a development
    checkout, the test suite) the grids come from the package - and they live
    in its kennfeld/ folder, not next to the modules."""
    hass = SimpleNamespace(config=SimpleNamespace(config_dir="/nowhere"))

    assert get_filepath(hass) == KENNFELD


def test_the_default_grid_ships_compiled_with_its_plot():
    """The config flow offers this file by default; a missing grid would be
    compiled at first start, on the user's machine, into the package."""
    default = KENNFELD / CONST.DEF_KENNFELDFILE

    assert default.exists()
    assert "compiled_grid" in json.loads(default.read_text(encoding="utf-8"))
    assert default.with_suffix(".svg").exists()


def test_every_shipped_grid_is_compiled_and_plotted():
    incomplete = []
    for grid in sorted(KENNFELD.glob("*.json")):
        data = json.loads(grid.read_text(encoding="utf-8"))
        if "compiled_grid" not in data or not grid.with_suffix(".svg").exists():
            incomplete.append(grid.name)

    assert not incomplete, (
        f"grid(s) that would be compiled or plotted at runtime: {incomplete}"
    )
