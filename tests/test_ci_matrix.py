"""The CI workflow must test the Home Assistant release it claims to test.

A job that installs pytest-homeassistant-custom-component unpinned reads like
"newest Home Assistant" but is not: the plugin ships a normal, non-prerelease
version for every Home Assistant BETA too, so pip resolves to one pinning a
beta. Nothing then covers the release people actually run, and the job stays
green while saying "current HA".

The "minimum" job is the other end: it pins the plugin release that ships
exactly the Home Assistant version hacs.json declares, so an API that does
not exist yet on that release is caught before a user meets it.

Only the selection logic is exercised - no PyPI access, so this stays a fast
unit test rather than a network-dependent one.
"""

import importlib.util
import json
from pathlib import Path
import re

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / ".github" / "scripts" / "resolve_phcc.py"
WORKFLOW = REPO / ".github" / "workflows" / "test.yaml"
TEST_REQUIREMENTS = REPO / "requirements_test.txt"
GATE_PINS = REPO / "requirements_gates.txt"
# The gates that must judge a change the same way on a laptop and in CI,
# and the version parser the CI resolver needs.
GATE_TOOLS = ("ruff", "mypy", "pip-audit", "packaging")
# Installed by name: the test plugin pins it, a pin here would fight that one.
PINNED_BY_THE_PLUGIN = {"pytest-timeout"}


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
        ("homeassistant==2026.8.0.dev0", True),
        # A range is not a pin: pip would pick, and it picks betas.
        ("homeassistant>=2026.8.0", True),
        ("not a requirement at all", True),
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


def _minimum_job_label() -> str:
    labels = re.findall(
        r'- name: "(minimum[^"]*)"', WORKFLOW.read_text(encoding="utf-8")
    )
    assert len(labels) == 1, f"expected one minimum job, found {labels}"
    return labels[0]


def test_the_minimum_job_names_the_version_hacs_declares():
    """Three places say which Home Assistant is the oldest supported one -
    hacs.json, the pinned plugin release, and the job's own name. The pin can
    only be checked with PyPI (check_min_ha.py does that inside the job); the
    label can be checked here, and it is the half that lies to a reader."""
    declared = json.loads((REPO / "hacs.json").read_text(encoding="utf-8"))[
        "homeassistant"
    ]
    release = ".".join(declared.split(".")[:2])

    assert release in _minimum_job_label(), (
        f"hacs.json declares {declared} as the minimum, but the matrix job is "
        f'called "{_minimum_job_label()}". Raise the job name AND its pinned '
        "plugin release together - the pin is what decides what is tested."
    )


def test_the_minimum_job_actually_checks_the_version_it_installed():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "check_min_ha.py" in workflow, (
        "the minimum job does not verify the Home Assistant it installed, so "
        "a wrong pin runs green under the right label"
    )
    assert re.search(r"if:\s*matrix\.check-declared-minimum", workflow)
    assert "check-declared-minimum: true" in workflow, (
        "no matrix entry opts into the check, so it never runs"
    )


def test_a_patch_release_still_counts_as_the_declared_minimum():
    """hacs.json names a patch; the plugin pins whichever patch of that
    feature release it ships. Comparing them literally would fail the job for
    being right."""
    namespace: dict = {}
    source = (REPO / ".github" / "scripts" / "check_min_ha.py").read_text(
        encoding="utf-8"
    )
    body = source[source.index("def feature_release") : source.index("def main")]
    exec(compile(body, "check_min_ha.py", "exec"), namespace)  # noqa: S102
    feature_release = namespace["feature_release"]

    assert feature_release("2026.9.4") == feature_release("2026.9.0")
    assert feature_release("2026.10.0") != feature_release("2026.9.0")


def test_the_test_job_resolves_a_final_home_assistant():
    steps = _steps_of("pytest")

    assert any("resolve_phcc.py" in line for line in steps), (
        "the test job installs the plugin some other way than through the "
        "resolver, so it can land on a Home Assistant beta"
    )
    assert any('phcc: "latest-stable-ha"' in line for line in steps), (
        "no matrix entry asks the resolver for the newest final release"
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
    assert "pip install -c requirements_gates.txt ruff" in workflow


def test_the_test_job_installs_the_ruff_a_test_runs():
    """The capture tool's formatter test runs ruff, and these jobs skip
    requirements_test.txt, the one file that would have brought it."""
    steps = _steps_of("pytest")

    assert any("pip install -c requirements_gates.txt ruff" in line for line in steps)


def test_the_secret_scan_in_ci_uses_the_repositorys_own_rules():
    """The allowlist for the register table lives in .gitleaks.toml. The
    action does not read it unless told, and then flags the translation keys
    as credentials - a red job for a file with no secret in it."""
    steps = _steps_of("gitleaks")

    assert any("GITLEAKS_CONFIG:" in line for line in steps), (
        "the gitleaks job does not name .gitleaks.toml, so CI scans with "
        "different rules than check.sh and the pre-push hook"
    )
    assert any(".gitleaks.toml" in line for line in steps)


def _pins_of(text: str) -> dict[str, str]:
    """Every `tool==version` in a text, whatever quotes it sits in."""
    return {
        tool: version
        for tool, version in re.findall(r"([a-z-]+)==([\d.]+)", text)
        if tool in GATE_TOOLS
    }


def test_the_workflow_and_the_local_gates_pin_the_same_versions():
    """check.sh runs what requirements_test.txt installs, CI what the
    workflow installs. The pins stood in both, and Dependabot moved only
    the first: its pull request went red on a disagreement it made. One
    file holds them now, and both read it."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    test_requirements = TEST_REQUIREMENTS.read_text(encoding="utf-8")

    assert set(_pins_of(GATE_PINS.read_text(encoding="utf-8"))) == set(GATE_TOOLS)
    assert "-r requirements_gates.txt" in test_requirements.splitlines()
    assert not _pins_of(test_requirements), "a second pin in requirements_test.txt"
    assert not _pins_of(workflow), "a second pin in the workflow"


def _unpinned(lines: list[str]) -> list[str]:
    """The pip installs among `lines` that leave a version to the day."""
    gate_pins = set(_pins_of(GATE_PINS.read_text(encoding="utf-8")))
    unpinned = []
    for line in lines:
        if "pip install" not in line or line.lstrip().startswith("#"):
            continue
        words = [
            word.strip("'\"").lower()
            for word in line.split("pip install", 1)[1].split()
        ]
        constrained = "-c" in words and "requirements_gates.txt" in words
        names = {
            word
            for word in words
            if re.fullmatch(r"[a-z][\w.-]*", word) and not word.endswith(".txt")
        }
        pinned = gate_pins if constrained else PINNED_BY_THE_PLUGIN
        if "--upgrade" in words or names - pinned:
            unpinned.append(line.strip())
    return unpinned


def test_every_pip_install_in_the_workflow_takes_its_version_from_a_pin():
    """CI installed packaging and the newest pip by name alone, whatever
    they were that day."""
    unpinned = _unpinned(WORKFLOW.read_text(encoding="utf-8").splitlines())

    assert not unpinned, f"pip install without a pinned version: {unpinned}"


@pytest.mark.parametrize(
    "line",
    [
        "pip install packaging",
        "pip install --upgrade pip",
        'pip install "packaging"',
        "pip install PyYAML",
        "pip install -c requirements_gates.txt pygal",
    ],
)
def test_the_pin_check_sees_an_unpinned_install_however_it_is_written(line):
    """Quoted, capitalised, or constrained by a file that has no pin for it,
    a name went through: the check read the spelling, not the pin."""
    assert _unpinned([f"        run: {line}"])


def test_the_step_reader_skips_comments_and_stops_at_the_next_job():
    """Proof-of-red for the helper the checks above rely on."""
    steps = _steps_of("mypy")

    assert steps and steps[0].strip() == "mypy:"
    assert not any(line.strip().startswith("#") for line in steps)
    assert not any(line.strip() == "pytest:" for line in steps), (
        "the reader ran into the next job"
    )


def test_ci_scans_with_a_gitleaks_the_local_floor_would_accept():
    """The allowlist is anchored on the exact match a rule reports, and that
    spelling changed between 8.16 and 8.30. Left to the action's default, CI
    would decide which spelling it is."""
    check = (REPO / ".github" / "scripts" / "check.sh").read_text(encoding="utf-8")
    floor = re.search(r"GITLEAKS_MIN=([\d.]+)", check).group(1)
    workflow = WORKFLOW.read_text(encoding="utf-8")
    pinned = re.findall(r'GITLEAKS_VERSION:\s*"([\d.]+)"', workflow)

    assert len(pinned) == 1, f"one pin for the whole workflow, found {pinned}"
    version = tuple(int(part) for part in pinned[0].split("."))
    assert version >= tuple(int(part) for part in floor.split(".")), (
        f"CI scans with gitleaks {pinned[0]}, check.sh demands {floor}"
    )
    assert "Install gitleaks for the secret-scan controls" in workflow, (
        "without the binary the secret-scan controls skip, and a skipped "
        "control cannot fail"
    )


def test_ci_and_check_sh_hold_coverage_to_the_same_floor():
    """The floor stood twice, and nothing noticed one of them moving alone."""
    check = (REPO / ".github" / "scripts" / "check.sh").read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    floors = re.findall(r"--cov-fail-under=([\d.]+)", check + workflow)

    assert len(floors) == 2, f"one floor in check.sh, one in CI: {floors}"
    assert floors[0] == floors[1], f"check.sh and CI disagree: {floors}"


def test_no_step_hides_a_failure_behind_a_pipe():
    """A pipeline reports the last command's status. A resolver that died
    still left tee returning 0, so the job ran on without the version it had
    just failed to fetch and failed two steps later on something else."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    piped = []
    for name, job in workflow["jobs"].items():
        for step in job["steps"]:
            script = step.get("run", "")
            pipes = re.search(r"(?<!\|)\|(?!\|)", script)
            if pipes and "pipefail" not in script:
                piped.append(f"{name}: {step.get('name', 'unnamed step')}")

    assert not piped, f"these steps pipe without pipefail: {piped}"


def test_each_test_lane_installs_the_modbus_stack_its_home_assistant_pins():
    """requirements.txt bounds modbus-connection from below on purpose, so a
    plain install resolved a newer release than the tested Home Assistant
    pins and the lane tested a combination no installation has."""
    steps = _steps_of("pytest")
    joined = "\n".join(steps)
    install = joined.index('pip install "$PHCC_SPEC"')
    pinned = joined.index("core_modbus_requirements.py")
    tests_run = joined.index("Run test suite")

    assert install < pinned < tests_run, (
        "core's Modbus pins must go on after the plugin install and before the tests"
    )
    assert "core_modbus_requirements.py --verify" in joined, (
        "installing is not enough: the versions have to be checked afterwards"
    )


def test_every_workflow_runs_on_pull_requests_and_pushes_to_main_only():
    """An unfiltered push ran every job twice per branch (push and pull
    request) and once more per tag, on a commit that was already green.
    And `branches` next to `branches-ignore` on one event makes GitHub reject
    the whole file silently: no red cross, nothing runs at all."""
    for path in sorted(WORKFLOW.parent.glob("*.y*ml")):
        triggers = yaml.safe_load(path.read_text(encoding="utf-8"))[True]
        push = triggers.get("push") or {}

        assert "pull_request" in triggers, f"{path.name}: no pull_request trigger"
        assert push.get("branches") == ["main"], (
            f"{path.name}: push must be filtered to main, got {push}"
        )
        assert "branches-ignore" not in push, (
            f"{path.name}: branches and branches-ignore together invalidate the file"
        )
