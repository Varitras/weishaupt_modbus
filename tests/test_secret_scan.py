"""The secret scanner still catches a token next to something it is told to ignore.

The allowlist in .gitleaks.toml exists because the generic-api-key rule reads
every `translation_key="..."` in the register table as a credential. Matched
against the whole LINE, that exception also hid a real token that sat on the
same line (audit 2026-09-03, reproduced with a synthetic key). It is matched
against the rule's match now - and this test is the proof that a second token
on the line is still reported, and that a plain translation key is not.

Needs the gitleaks binary; the pre-push hook and check.sh need it anyway.
"""

import pathlib
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
CONFIG = REPO / ".gitleaks.toml"

# Synthetic. The AWS access-key rule matches AKIA + 16 characters; this one
# is not a key anyone was ever issued.
SYNTHETIC_TOKEN = "AKIAQ7Q7Q7Q7Q7Q7Q7Q7"
LEAK_FOUND = 2  # gitleaks' exit code for "findings" with --exit-code 2


def _scan(tmp_path: pathlib.Path, source: str) -> int:
    (tmp_path / "table.py").write_text(source, encoding="utf-8")
    result = subprocess.run(
        [
            "gitleaks",
            "detect",
            "--no-git",
            "--no-banner",
            "--redact",
            "--config",
            str(CONFIG),
            "--source",
            str(tmp_path),
            "--exit-code",
            str(LEAK_FOUND),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode


@pytest.fixture(autouse=True)
def _gitleaks_installed():
    if shutil.which("gitleaks") is None:
        pytest.skip(
            "gitleaks is not installed here; check.sh and the pre-push hook require it"
        )


def test_a_translation_key_alone_is_not_a_finding(tmp_path):
    assert _scan(tmp_path, 'ModbusItem(address=1, translation_key="hp_konf_2")\n') == 0


def test_a_token_next_to_a_translation_key_is_still_found(tmp_path):
    line = f'ModbusItem(translation_key="hp_konf", token="{SYNTHETIC_TOKEN}")\n'
    assert _scan(tmp_path, line) == LEAK_FOUND


def test_a_hex_token_in_the_key_position_is_still_found(tmp_path):
    """A hex string has no letter beyond f and no underscore - the allowlist
    must not take it for a translation key."""
    line = 'api_key="9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e"\n'
    assert _scan(tmp_path, line) == LEAK_FOUND
