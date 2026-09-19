"""The Modbus libraries the installed Home Assistant pins, for the test job.

    python .github/scripts/core_modbus_requirements.py            # print them
    python .github/scripts/core_modbus_requirements.py --verify   # exit 1 if not installed

requirements.txt gives modbus-connection a lower bound on purpose: core's own
modbus integration pins it exactly, and a custom component pinning another
version cannot be installed beside it. Left to that bound, CI resolved a newer
release than any installation of the tested Home Assistant has, and the job
vouched for a combination nobody runs. The pins come from the manifest of the
Home Assistant that is installed, so each matrix lane tests its own release's.
"""

from importlib import metadata, resources
import json
import re
import sys


def core_modbus_requirements() -> list[str]:
    manifest = resources.files("homeassistant.components.modbus").joinpath(
        "manifest.json"
    )
    requirements: list[str] = json.loads(manifest.read_text(encoding="utf-8"))[
        "requirements"
    ]
    return requirements


def _installed_matches(requirement: str) -> bool:
    name, version = re.fullmatch(
        r"([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==(\S+)", requirement
    ).groups()
    return metadata.version(name) == version


def main(argv: list[str]) -> int:
    requirements = core_modbus_requirements()
    if argv[1:] != ["--verify"]:
        print("\n".join(requirements))
        return 0
    mismatched = [r for r in requirements if not _installed_matches(r)]
    for requirement in mismatched:
        print(
            f"installed version differs from core's pin: {requirement}", file=sys.stderr
        )
    return 1 if mismatched else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
