"""Every module imports outside the author's own tree.

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

# The command-line probe runs its main() at import and is not a module of the
# integration; everything else has to import cleanly.
NOT_A_MODULE = {"weishaupt_modbus_api/__main__.py"}


def _modules():
    for source in sorted(PACKAGE.rglob("*.py")):
        relative = source.relative_to(PACKAGE).as_posix()
        if relative in NOT_A_MODULE:
            continue
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
