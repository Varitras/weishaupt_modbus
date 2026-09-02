"""The power map: interpolation on the compiled grid, and the shipped grids."""

import importlib
import importlib.util
import json
import logging
import pathlib
import re
from types import SimpleNamespace

import pytest

from custom_components.weishaupt_modbus import kennfeld
from custom_components.weishaupt_modbus.const import CONF, CONST
from custom_components.weishaupt_modbus.kennfeld import PowerMap, get_filepath

PACKAGE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "weishaupt_modbus"
)

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


def test_importing_the_module_does_not_warn_about_optional_libraries(
    monkeypatch, caplog
):
    """Every shipped grid is precompiled, so numpy and scipy are only needed
    to compile a NEW grid - and that path says so when it runs. Warning at
    import time put three lines into every user's log on every start for a
    situation none of them was in."""
    real_find_spec = importlib.util.find_spec

    def without_the_optional_libraries(name, *args, **kwargs):
        if name in ("numpy", "scipy", "pygal"):
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", without_the_optional_libraries)
    try:
        with caplog.at_level(logging.WARNING, logger=kennfeld.__name__):
            importlib.reload(kennfeld)
    finally:
        monkeypatch.undo()
        importlib.reload(kennfeld)

    warned = [
        record.getMessage()
        for record in caplog.records
        if record.name == kennfeld.__name__
    ]
    assert not warned, f"import-time noise: {warned}"


def test_the_preview_file_is_named_per_entry():
    """Two pumps wrote the same www/local/…_powermap.svg and raced for it."""
    assert (
        kennfeld.powermap_file_name({CONF.DEVICE_POSTFIX: ""})
        == "weishaupt_modbus_powermap.svg"
    )
    assert (
        kennfeld.powermap_file_name({CONF.DEVICE_POSTFIX: "2"})
        == "weishaupt_modbus_powermap_2.svg"
    )


def test_shipped_plots_are_static_pictures():
    """pygal's default SVG carries inline script and fetches tooltip JavaScript
    from the web; copied under Home Assistant's www it would run in its
    origin if ever embedded actively."""
    shipped = list((PACKAGE / "kennfeld").glob("*.svg"))
    assert shipped
    for plot in shipped:
        text = plot.read_text(encoding="utf-8")
        assert "<script" not in text, f"{plot.name} carries script"
        assert not re.search(r'href="https?://', text), f"{plot.name} links to the web"
