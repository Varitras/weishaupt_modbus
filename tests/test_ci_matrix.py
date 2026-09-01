"""The CI workflow must test the Home Assistant release it claims to test.

A job that installs pytest-homeassistant-custom-component unpinned reads like
"newest Home Assistant" but is not: the plugin ships a normal, non-prerelease
version for every Home Assistant BETA too, so pip resolves to one pinning a
beta. Nothing then covers the release people actually run, and the job stays
green while saying "current HA".

There is no "minimum" job here: hacs.json declares no minimum Home Assistant
version, so there is no floor to test against. The day it declares one, the
matrix gets its second end and this file its second half.

Only the selection logic is exercised - no PyPI access, so this stays a fast
unit test rather than a network-dependent one.
"""

import importlib.util
from pathlib import Path
import re

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / ".github" / "scripts" / "resolve_phcc.py"
WORKFLOW = REPO / ".github" / "workflows" / "test.yaml"


def _load():
    spec = importlib.util.spec_from_file_location("resolve_phcc", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resolve_phcc = _load()


@pytest.mark.parametrize(
    ("pin", "expected"),
    [
        ("homeassistant==2026.7.4", False),
        ("homeassistant==2026.8.0b3", True),
        ("homeassistant==2026.8.0rc1", True),
        ("homeassistant==2026.8.0a1", True),
        # No pin at all: we cannot tell what would be installed, and the whole
        # point of the script is not having to guess.
        (None, True),
    ],
)
def test_a_prerelease_pin_is_recognised(pin, expected):
    assert resolve_phcc.is_prerelease(pin) is expected


def test_the_newest_final_release_wins_over_newer_betas():
    pins = {
        "0.13.351": "homeassistant==2026.8.0b3",
        "0.13.350": "homeassistant==2026.8.0b2",
        "0.13.348": "homeassistant==2026.7.4",
        "0.13.347": "homeassistant==2026.7.3",
    }

    assert resolve_phcc.newest_stable(pins) == "0.13.348"


def test_versions_are_compared_numerically_not_as_text():
    """ "0.13.9" must not outrank "0.13.348" - which it does as a string."""
    pins = {
        "0.13.9": "homeassistant==2026.1.0",
        "0.13.348": "homeassistant==2026.7.4",
    }

    assert resolve_phcc.newest_stable(pins) == "0.13.348"


def test_nothing_usable_fails_loudly():
    """Falling back to "install whatever" would put the beta straight back."""
    with pytest.raises(SystemExit):
        resolve_phcc.newest_stable({"0.13.351": "homeassistant==2026.8.0b3"})


def test_an_explicit_requirement_is_passed_through(capsys):
    """A pinned entry must reach pip unchanged - and must not trigger a PyPI
    lookup."""
    resolve_phcc.main(
        ["resolve_phcc.py", "pytest-homeassistant-custom-component==0.13.190"]
    )

    assert capsys.readouterr().out.strip() == (
        "PHCC_SPEC=pytest-homeassistant-custom-component==0.13.190"
    )


def _steps_of(job: str) -> list:
    """The lines of one workflow job that DO something - not the comments
    that talk about it. A comment explaining why a file is NOT used satisfies
    a plain substring search for that file."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index(f"  {job}:")
    rest = workflow[start + 1 :]
    end = re.search(r"^  \w+:\s*$", rest, flags=re.MULTILINE)
    body = workflow[start : start + 1 + end.start()] if end else workflow[start:]
    return [line for line in body.splitlines() if not line.strip().startswith("#")]


def test_the_test_job_resolves_a_final_home_assistant():
    steps = _steps_of("pytest")

    assert any("resolve_phcc.py latest-stable-ha" in line for line in steps), (
        "the test job installs the plugin some other way than through the "
        "resolver, so it can land on a Home Assistant beta"
    )
    assert not [line for line in steps if "requirements_test.txt" in line], (
        "the unpinned requirements file is back in the test job"
    )


def test_the_test_job_runs_the_whole_suite_and_the_mutations():
    steps = _steps_of("pytest")

    assert any('pytest tests/ -q -m ""' in line for line in steps), (
        'the e2e tests are deselected by default; CI has to pass -m ""'
    )
    assert any("mutate.py .github/mutations" in line for line in steps), (
        "a green suite says the tests pass, not that they would catch anything"
    )


def test_the_type_check_runs_the_same_home_assistant_as_the_test_job():
    """A checker that vouches for another release vouches for nothing."""
    steps = _steps_of("mypy")

    assert any("resolve_phcc.py latest-stable-ha" in line for line in steps), (
        "the type check resolves its Home Assistant some other way than the "
        "job whose type surface it is supposed to be checking"
    )
    assert not [line for line in steps if "requirements_test.txt" in line], (
        "the unpinned requirements file is back, so this job can install a "
        "Home Assistant beta the tests never run against"
    )


def test_the_workflow_still_runs_ruff_pinned():
    """Formatting that only one machine checks survives until the first
    commit written somewhere else; an unpinned Ruff enforces whatever it
    decided this week."""
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "run: ruff check ." in workflow
    assert "run: ruff format --check ." in workflow
    assert re.search(r'pip install "ruff==[\d.]+"', workflow)


def test_the_step_reader_skips_comments_and_stops_at_the_next_job():
    """Proof-of-red for the helper the checks above rely on."""
    steps = _steps_of("mypy")

    assert steps and steps[0].strip() == "mypy:"
    assert not any(line.strip().startswith("#") for line in steps)
    assert not any(line.strip() == "pytest:" for line in steps), (
        "the reader ran into the next job"
    )
