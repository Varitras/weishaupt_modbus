"""Every platform builds its entities through the one shared helper.

`build_entity_list` in entity_helpers.py is the single place that decides
which ModbusItem becomes which entity class. The sensor platform once built
its Modbus and WebIF lists into one and registered them together, so an error
in the optional WebIF part silently dropped every Modbus sensor (issue #172).
The cure was one helper and one call per platform - and it would be easy to
write the old loop again in a fourth platform, or back into one of these, with
nothing about the result looking wrong.

Driven off PLATFORMS rather than a list written out here, so a platform added
later is covered without anyone remembering to come back.
"""

import ast
import pathlib

from custom_components.weishaupt_modbus import PLATFORMS

PACKAGE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "weishaupt_modbus"
)

THE_SHARED_WAY = "build_entity_list"


def _platform_sources() -> dict:
    sources = {}
    for platform in PLATFORMS:
        source_file = PACKAGE / f"{platform}.py"
        assert source_file.exists(), (
            f"{platform} is in PLATFORMS but {source_file.name} does not exist - "
            "either the platform moved or this scan has gone blind"
        )
        sources[source_file.name] = source_file.read_text(encoding="utf-8")
    return sources


def _calls_the_shared_helper(source: str) -> bool:
    """Whether a module CALLS the helper, rather than merely naming it.

    An import names it. Every platform imports it at the top, so a substring
    scan would be answered by that line alone: delete the actual call and the
    guard would stay green while the platform stopped producing entities.
    """
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == THE_SHARED_WAY
        for node in ast.walk(ast.parse(source))
    )


def test_the_scan_is_not_satisfied_by_the_import_line():
    imports_only = (
        f"from .entity_helpers import {THE_SHARED_WAY}\n\n\nasync def setup():\n"
        "    pass\n"
    )
    assert not _calls_the_shared_helper(imports_only)
    assert _calls_the_shared_helper(f"entries = await {THE_SHARED_WAY}(entries=[])\n")


def test_every_platform_builds_its_entities_through_the_shared_helper():
    missing = [
        name
        for name, source in _platform_sources().items()
        if not _calls_the_shared_helper(source)
    ]

    assert not missing, (
        f"{missing} do(es) not call {THE_SHARED_WAY}, so the decision which "
        "item becomes which entity lives in two places. Call the helper in "
        "entity_helpers.py instead of walking the item list here."
    )


def test_every_platform_in_the_list_is_a_module():
    """The scan above reads PLATFORMS; a platform named there without a module
    would fail at Home Assistant's forward-setup, not here, and this says so
    first."""
    assert PLATFORMS, "no platforms at all - the scan proves nothing"
    assert set(_platform_sources()) == {f"{platform}.py" for platform in PLATFORMS}
