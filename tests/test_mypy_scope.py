"""Every module is type-checked, or says in one line why it is not yet.

The mypy scope in pyproject.toml was frozen at the modules that were clean
when the gate was adopted. Nothing would otherwise stop a module added to the
package from simply not being checked - the job stays green because the file
is never named, which is the same shape as a guard that goes blind.

This does not demand that everything be typed. It demands that leaving a
module out be a decision somebody wrote down.

The scope is READ from pyproject.toml rather than repeated here. A copy would
be the second place the list lives, and the first to drift.
"""

import pathlib
import tomllib

REPO = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = REPO / "custom_components" / "weishaupt_modbus"

# What is deliberately still outside, and why - the error count mypy reported
# on adoption. Emptying this list is the point; every entry is a promise, not
# a permanent exemption.
NOT_TYPE_CHECKED_YET = {
    "configentry.py": "1 error: names a MyCoordinator the coordinator module no longer has",
    "coordinator.py": "12 errors: Any returns and an optional config_entry read without a guard",
    "entities.py": "12 errors: MyEntity reads a coordinator attribute only its subclasses have",
    "items.py": "7 errors: unreachable None checks and Any returns from the lookups",
    "kennfeld.py": "2 errors: an optional dict used as if it were present",
    "modbusobject.py": "2 errors: unreachable statements in the legacy client",
    "translations.py": "3 errors: dicts assigned into str-typed slots",
    "weishaupt_modbus_api/__main__.py": "4 errors: untyped command-line entry point",
    "weishaupt_modbus_api/modbus_api.py": "6 errors: list concatenation typed as item addition",
}


def _modules_in_the_mypy_scope() -> set:
    """The file list from pyproject.toml, relative to the package - minus any
    module an override tells mypy to ignore errors in. A file that is named
    only so mypy resolves it, with its errors switched off, is not checked."""
    configuration = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    mypy = configuration["tool"]["mypy"]
    prefix = "custom_components/weishaupt_modbus/"
    ignored = {
        override["module"].removeprefix("custom_components.weishaupt_modbus.")
        for override in mypy.get("overrides", [])
        if override.get("ignore_errors")
        and override["module"].startswith("custom_components.weishaupt_modbus.")
    }
    return {
        entry.removeprefix(prefix)
        for entry in mypy["files"]
        if entry.removeprefix(prefix).removesuffix(".py").replace("/", ".")
        not in ignored
    }


def _modules_in_the_package() -> set:
    return {source.relative_to(PACKAGE).as_posix() for source in PACKAGE.rglob("*.py")}


def test_every_module_is_either_checked_or_declared():
    """A module in neither list is one nobody decided about."""
    unaccounted = (
        _modules_in_the_package()
        - _modules_in_the_mypy_scope()
        - set(NOT_TYPE_CHECKED_YET)
    )

    assert not unaccounted, (
        f"{sorted(unaccounted)} are neither in the mypy scope nor declared "
        "here. Add them to `files` in pyproject.toml, or add a line above "
        "saying what is in the way."
    )


def test_nothing_is_declared_unchecked_and_checked_at_once():
    """An entry kept after its module was typed reads like a warning that no
    longer applies, and the next person believes it."""
    both = _modules_in_the_mypy_scope() & set(NOT_TYPE_CHECKED_YET)

    assert not both, (
        f"{sorted(both)} are type-checked now - drop them from "
        "NOT_TYPE_CHECKED_YET, which is what emptying it looks like."
    )


def test_no_promise_outlives_its_module():
    """A note about a file that is gone is a note nobody can act on."""
    ghosts = set(NOT_TYPE_CHECKED_YET) - _modules_in_the_package()

    assert not ghosts, f"{sorted(ghosts)} no longer exist; drop the entries."


def test_the_scan_would_notice_a_module_nobody_decided_about():
    """The shape it exists to catch, fed directly: a new module in the package
    that neither list mentions. Spelled out rather than created on disk, because
    writing into the package during a test run is a side effect a suite should
    not have."""
    package = {"items.py", "brand_new.py"}
    scope = {"items.py"}
    declared = set()

    assert package - scope - declared == {"brand_new.py"}
    assert package - scope - {"brand_new.py"} == set()
