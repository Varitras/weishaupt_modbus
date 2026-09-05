"""The power map: interpolation on the compiled grid, and the shipped grids."""

import ast
import importlib
import importlib.util
import json
import logging
import pathlib
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


def test_an_empty_grid_reads_as_none_not_zero():
    """A missing or uncompiled map used to read as 0 W - recorded as a real
    heat output of zero."""
    power_map = _power_map()
    power_map._compiled_grid = {}

    assert power_map.map(0, 350) is None


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


async def test_a_grid_that_is_not_compiled_is_refused_not_compiled(
    hass, caplog, tmp_path, monkeypatch
):
    """Compiling needs numpy, which no user has; the integration used to try,
    fail on most hosts and leave the heat power at zero with three warnings.
    Now it says what is missing and leaves the map empty."""
    raw = {
        "known_x": [-10, 10],
        "known_y": [[5000, 6000], [4000, 5000]],
        "known_t": [35, 55],
    }
    (tmp_path / "raw_kennfeld.json").write_text(json.dumps(raw), encoding="utf-8")
    entry = SimpleNamespace(
        data={CONF.KENNFELD_FILE: "raw_kennfeld.json", CONF.DEVICE_POSTFIX: ""}
    )
    monkeypatch.setattr(kennfeld, "get_filepath", lambda _hass: tmp_path)
    power_map = PowerMap(entry, hass)

    with caplog.at_level(logging.ERROR, logger=kennfeld.__name__):
        await power_map.initialize()

    assert "no compiled grid" in caplog.text
    assert "compiled_grid" not in json.loads(
        (tmp_path / "raw_kennfeld.json").read_text(encoding="utf-8")
    ), "the integration compiled the grid after all"
    assert power_map.map(0, 350) is None


OPTIONAL_LIBRARIES = {"numpy", "scipy", "pygal"}


def _module_level(source: pathlib.Path) -> tuple[set[str], set[str]]:
    """The packages a module imports at top level, and the calls it makes there."""
    module = ast.parse(source.read_text(encoding="utf-8"))
    imports = {
        (
            node.names[0].name if isinstance(node, ast.Import) else node.module or ""
        ).split(".")[0]
        for node in module.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    }
    calls = {
        ast.unparse(node.value.func)
        for node in module.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    }
    return imports, calls


def test_importing_the_integration_needs_no_optional_library_and_says_nothing():
    """Every shipped grid is precompiled; numpy and scipy are only needed to
    compile a NEW grid, pygal only to draw one. Importing them at module level
    used to put three warning lines into every user's log on every start for
    a situation none of them was in.

    Read statically over the whole package: a reload with a patched finder
    proved nothing, because the module never consulted the finder.
    """
    offenders = []
    for source in sorted(PACKAGE.rglob("*.py")):
        imports, calls = _module_level(source)
        for library in sorted(imports & OPTIONAL_LIBRARIES):
            offenders.append(f"{source.name} imports {library} at module level")
        for call in sorted(call for call in calls if call.startswith("_LOGGER.")):
            offenders.append(f"{source.name} logs at import time: {call}")

    assert not offenders, offenders


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


async def test_a_preview_that_carries_script_is_not_copied_under_www(
    hass, caplog, tmp_path, monkeypatch
):
    """A custom grid may come with a picture from anywhere; copied unchanged
    under www/ it would run in Home Assistant's origin. The shipped
    pictures were already checked; the copy path was not."""
    grid = {
        "known_t": [35, 55],
        "known_x": [-10, 10],
        "compiled_grid": {"0": [1.0, 2.0]},
    }
    (tmp_path / "own_kennfeld.json").write_text(json.dumps(grid), encoding="utf-8")
    (tmp_path / "own_kennfeld.svg").write_text(
        "<svg><script>alert(1)</script></svg>", encoding="utf-8"
    )
    entry = SimpleNamespace(
        data={CONF.KENNFELD_FILE: "own_kennfeld.json", CONF.DEVICE_POSTFIX: ""}
    )
    monkeypatch.setattr(kennfeld, "get_filepath", lambda _hass: tmp_path)
    www = tmp_path / "www" / "local"
    hass.config.config_dir = str(tmp_path)

    with caplog.at_level(logging.ERROR, logger=kennfeld.__name__):
        await PowerMap(entry, hass).initialize()

    assert not (www / "weishaupt_modbus_powermap.svg").exists()
    assert "carries script" in caplog.text


async def test_a_static_preview_is_copied_under_www(hass, tmp_path, monkeypatch):
    grid = {
        "known_t": [35, 55],
        "known_x": [-10, 10],
        "compiled_grid": {"0": [1.0, 2.0]},
    }
    (tmp_path / "own_kennfeld.json").write_text(json.dumps(grid), encoding="utf-8")
    (tmp_path / "own_kennfeld.svg").write_text(
        "<svg><path d='M0 0'/></svg>", encoding="utf-8"
    )
    entry = SimpleNamespace(
        data={CONF.KENNFELD_FILE: "own_kennfeld.json", CONF.DEVICE_POSTFIX: ""}
    )
    monkeypatch.setattr(kennfeld, "get_filepath", lambda _hass: tmp_path)
    hass.config.config_dir = str(tmp_path)

    await PowerMap(entry, hass).initialize()

    assert (tmp_path / "www" / "local" / "weishaupt_modbus_powermap.svg").exists()


def test_shipped_plots_are_static_pictures():
    """pygal's default SVG carries inline script and fetches tooltip JavaScript
    from the web; copied under Home Assistant's www it would run in its
    origin if ever embedded actively."""
    shipped = list((PACKAGE / "kennfeld").glob("*.svg"))
    assert shipped
    for plot in shipped:
        assert kennfeld.is_static_picture(plot.read_text(encoding="utf-8")), plot.name


@pytest.mark.parametrize(
    ("content", "why"),
    [
        ("{not json", "corrupt JSON"),
        ('{"known_t": [35], "compiled_grid": {"0": [1.0]}}', "one flow curve only"),
        (
            '{"known_t": [35, 55], "compiled_grid": {"0": [1.0]}}',
            "row shorter than known_t",
        ),
        ("[1, 2, 3]", "not an object"),
    ],
)
async def test_a_broken_grid_file_disables_the_heat_power_only(
    hass, caplog, tmp_path, monkeypatch, content, why
):
    """A corrupt custom grid raised out of setup and Home Assistant put the
    whole entry into SETUP_ERROR - every Modbus entity gone for one optional
    calculated sensor."""
    (tmp_path / "bad_kennfeld.json").write_text(content, encoding="utf-8")
    entry = SimpleNamespace(
        data={CONF.KENNFELD_FILE: "bad_kennfeld.json", CONF.DEVICE_POSTFIX: ""}
    )
    monkeypatch.setattr(kennfeld, "get_filepath", lambda _hass: tmp_path)
    power_map = PowerMap(entry, hass)

    with caplog.at_level(logging.ERROR, logger=kennfeld.__name__):
        await power_map.initialize()

    assert power_map.map(0, 350) is None, why
    assert "Power map" in caplog.text or "Failed to load" in caplog.text


def _compile_script():
    spec = importlib.util.spec_from_file_location(
        "compile_kennfeld",
        pathlib.Path(__file__).resolve().parents[1]
        / ".github"
        / "scripts"
        / "compile_kennfeld.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_compile_script_keeps_each_curve_with_its_flow_temperature():
    """It sorted known_t and left known_y in file order: an unsorted raw grid
    got its curves swapped."""
    known_t, known_y = _compile_script().sorted_curves(
        [55, 35], [[4000, 5000], [5000, 6000]]
    )

    assert known_t == [35, 55]
    assert known_y == [[5000, 6000], [4000, 5000]]


def test_the_integration_does_not_draw_pictures_at_runtime():
    """pygal left the manifest: drawing happens in compile_kennfeld.py. A
    module that imported it again would fail on every installation."""
    mentions = [
        source.name
        for source in PACKAGE.rglob("*.py")
        if "pygal" in source.read_text(encoding="utf-8")
    ]

    assert mentions == []
