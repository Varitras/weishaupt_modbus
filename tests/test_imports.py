"""Every module imports outside the author's own tree, and the web interface
without Home Assistant.

The incident this exists for: three imports of the form

    from config.custom_components.weishaupt_modbus.weishaupt_modbus_api ...

shipped on main. They resolve in one developer's Home Assistant container,
where the config directory is a package on sys.path, and nowhere else - the
integration raised ModuleNotFoundError on import for every user, and the
test suite could not even be collected. No test saw it, because no test
imported the package as a whole and CI ran no tests at all.

Two guards, both package-wide:

  1. Every module imports. A collection error is the loudest possible signal
     and the cheapest one to have.
  2. No import names a path outside `custom_components.weishaupt_modbus`.
     An absolute self-import spelled `custom_components.weishaupt_modbus.x`
     is fine - Home Assistant puts the config directory on sys.path, so that
     is the one absolute spelling that resolves everywhere.

And one for the web interface package, the client and the parser, which is
the layer below the coordinator and the sensors:

  3. Nothing in `webif/` imports Home Assistant, neither by name nor through
     the integration around the package, whose modules do.
"""

import ast
import importlib
import pathlib

import pytest

PACKAGE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "weishaupt_modbus"
)
PACKAGE_NAME = "custom_components.weishaupt_modbus"
WEB_INTERFACE = PACKAGE / "webif"
WEB_INTERFACE_NAME = f"{PACKAGE_NAME}.webif"


def _modules():
    for source in sorted(PACKAGE.rglob("*.py")):
        relative = source.relative_to(PACKAGE).as_posix()
        dotted = relative.removesuffix(".py").replace("/", ".")
        if dotted.endswith("__init__"):
            dotted = dotted.removesuffix(".__init__") or ""
        yield relative, f"{PACKAGE_NAME}.{dotted}" if dotted else PACKAGE_NAME


@pytest.mark.parametrize(("relative", "dotted"), list(_modules()))
def test_every_module_imports(relative, dotted):
    importlib.import_module(dotted)


def _foreign_absolute_imports(source: str) -> list:
    """Absolute imports that name a package tree this integration does not
    own but that LOOK like a path into it: anything under `config.` and any
    `custom_components.<other>`."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        for name in names:
            if (
                name == "config"
                or name.startswith("config.")
                or (
                    name.startswith("custom_components.")
                    and not (
                        name == PACKAGE_NAME or name.startswith(PACKAGE_NAME + ".")
                    )
                )
            ):
                found.append(name)
    return found


def test_no_module_imports_through_a_developer_tree():
    offenders = []
    for source in sorted(PACKAGE.rglob("*.py")):
        for name in _foreign_absolute_imports(source.read_text(encoding="utf-8")):
            offenders.append(f"{source.relative_to(PACKAGE).as_posix()}: {name}")

    assert not offenders, (
        f"import(s) through a tree only one machine has: {offenders}. Use a "
        f"relative import or `{PACKAGE_NAME}.<module>` - both resolve wherever "
        "Home Assistant loads the integration."
    )


def test_the_scan_catches_the_line_that_shipped():
    """Verbatim from the incident, and the two spellings that replaced it."""
    shipped = (
        "from config.custom_components.weishaupt_modbus.weishaupt_modbus_api"
        ".modbus_api import (\n    WeishauptModbusClient,\n)\n"
    )
    assert _foreign_absolute_imports(shipped) == [
        "config.custom_components.weishaupt_modbus.weishaupt_modbus_api.modbus_api"
    ]

    absolute = (
        "from custom_components.weishaupt_modbus.weishaupt_modbus_api.modbus_api "
        "import WeishauptModbusClient\n"
    )
    assert _foreign_absolute_imports(absolute) == []
    assert _foreign_absolute_imports("from .const import CONF\n") == []
    assert _foreign_absolute_imports("import config\n") == ["config"]
    assert _foreign_absolute_imports("from custom_components.hacs import x\n") == [
        "custom_components.hacs"
    ]


def _within(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


def _ties_to_home_assistant(name: str) -> bool:
    around_the_web_interface = _within(name, PACKAGE_NAME) and not _within(
        name, WEB_INTERFACE_NAME
    )
    return _within(name, "homeassistant") or around_the_web_interface


def _imports_tying_the_web_interface(source: str) -> list:
    """Imports of Home Assistant, or of the integration around the package -
    by name, or by a relative path out of it."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.level > 1:
            found.append("." * node.level + (node.module or ""))
            continue
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        found.extend(name for name in names if _ties_to_home_assistant(name))
    return found


def test_the_web_interface_imports_nothing_of_home_assistant():
    sources = sorted(WEB_INTERFACE.rglob("*.py"))
    offenders = [
        f"{source.relative_to(PACKAGE).as_posix()}: {name}"
        for source in sources
        for name in _imports_tying_the_web_interface(source.read_text(encoding="utf-8"))
    ]

    assert sources, "no web interface package found - the scan looks nowhere"
    assert not offenders, (
        f"the web interface imports Home Assistant: {offenders}. Hand the "
        "client what it needs from the layer above instead."
    )


def test_the_web_interface_scan_catches_each_way_in():
    """By name, through the integration - whose webif_sensor only shares the
    package's prefix - and by a relative path out of the package."""
    assert _imports_tying_the_web_interface(
        "from homeassistant.core import HomeAssistant\n"
    ) == ["homeassistant.core"]
    assert _imports_tying_the_web_interface(
        f"import {PACKAGE_NAME}.webif_sensor\n"
    ) == [f"{PACKAGE_NAME}.webif_sensor"]
    assert _imports_tying_the_web_interface("from ..const import CONF\n") == ["..const"]
    assert (
        _imports_tying_the_web_interface(
            f"from . import pages\nfrom {WEB_INTERFACE_NAME}.client import Client\n"
        )
        == []
    )
