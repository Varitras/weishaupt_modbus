"""Pick the Home Assistant test plugin release to test against.

The test matrix wants "the newest Home Assistant our users can actually be
running". The plugin was pytest-homeassistant-custom-component until it
ended with Home Assistant 2026.10; from 2026.11 Home Assistant ships its own,
pytest-homeassistant, versioned like Home Assistant itself. The old plugin
published a normal, non-prerelease version for every Home Assistant BETA, so
an unpinned install tested a beta and the job named "current HA" quietly
stopped covering the release people run. The new one marks its betas as
prereleases, but the check stays: the newest release whose Home Assistant pin
is a final version is chosen explicitly. A literal requirement (the pinned
minimum-version entry of the matrix, still on the old plugin) is passed
through untouched. The script and its variable keep the old plugin's name.

Usage:
    python resolve_phcc.py latest-stable-ha  ->  PHCC_SPEC=...==2026.11.2
    python resolve_phcc.py 'pkg==0.13.190'   ->  PHCC_SPEC=pkg==0.13.190
"""

from __future__ import annotations

import json
import sys
import urllib.request

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

PACKAGE = "pytest-homeassistant"
SENTINEL = "latest-stable-ha"


def is_prerelease(pin: str | None) -> bool:
    """Whether a `homeassistant==...` requirement names a prerelease.

    Anything that is not an exact pin to a final version counts as one: no
    pin, a range, a dev or local version - the uncertainty this script
    exists to remove.
    """
    if not pin:
        return True
    try:
        pinned = [
            spec.version for spec in Requirement(pin).specifier if spec.operator == "=="
        ]
        return len(pinned) != 1 or Version(pinned[0]).is_prerelease
    except InvalidRequirement, InvalidVersion:
        return True


def home_assistant_pin(requires_dist) -> str | None:
    """The homeassistant requirement among a release's dependencies."""
    for requirement in requires_dist or []:
        try:
            name = Requirement(requirement).name
        except InvalidRequirement:
            continue
        if name.lower() == "homeassistant":
            return requirement
    return None


def sort_key(version: str) -> Version:
    return Version(version)


def newest_stable(pins: dict[str, str | None]) -> str:
    """Return the highest version in `pins` whose Home Assistant is final."""
    for version in sorted(pins, key=sort_key, reverse=True):
        if not is_prerelease(pins[version]):
            return version
    raise SystemExit(
        f"No {PACKAGE} release pins a final Home Assistant version. "
        "Refusing to guess - fix the matrix by hand."
    )


def _fetch(url: str):
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def resolve_from_pypi(candidates: int = 20) -> str:
    index = _fetch(f"https://pypi.org/pypi/{PACKAGE}/json")
    published = [version for version, files in index["releases"].items() if files]
    recent = sorted(published, key=sort_key, reverse=True)[:candidates]
    pins = {
        version: home_assistant_pin(
            _fetch(f"https://pypi.org/pypi/{PACKAGE}/{version}/json")["info"].get(
                "requires_dist"
            )
        )
        for version in recent
    }
    return newest_stable(pins)


def main(argv: list[str]) -> int:
    requested = argv[1] if len(argv) > 1 else SENTINEL
    if requested != SENTINEL:
        spec = requested
    else:
        spec = f"{PACKAGE}=={resolve_from_pypi()}"
    # Printed as an environment assignment so the workflow can append it to
    # $GITHUB_ENV in one step. Also echoed to the log, since "which Home
    # Assistant did this run actually test?" is the first question when a
    # matrix job disagrees with a local run.
    print(f"PHCC_SPEC={spec}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
