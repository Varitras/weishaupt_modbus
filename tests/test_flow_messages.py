"""Every message the config flow can show has a text, in every language.

A reason or error key without a text renders as the raw key in the dialog.
`unknown` sat in all four files for a year without the flow ever producing
it, while the postfix rules arrived with none of theirs -
two directions of the same drift, and nothing looked at either.
"""

import ast
import json
import pathlib

import pytest

INTEGRATION = pathlib.Path(__file__).resolve().parents[1] / "custom_components"
FLOW = next(INTEGRATION.glob("*/config_flow.py"))
TRANSLATION_FILES = (
    "strings.json",
    "translations/en.json",
    "translations/de.json",
    "translations/nl.json",
)


def _flow_messages() -> set[str]:
    """Every abort reason and error key the flow hands to the frontend.

    Read out of the source rather than listed here: a list beside the flow is
    one more place to forget. The three shapes it uses are
    `async_abort(reason=...)`, an assignment into the `errors` dict, and a
    returned key (`namespace_error`).
    """
    messages = set()
    for node in ast.walk(ast.parse(FLOW.read_text(encoding="utf-8"))):
        if (
            (isinstance(node, ast.keyword) and node.arg == "reason")
            or (
                isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Subscript) for target in node.targets)
            )
            or isinstance(node, ast.Return)
        ):
            messages.add(node.value)
    return {
        node.value
        for node in messages
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _texts_of(name: str) -> set[str]:
    """Abort and error keys of both the config and the options flow."""
    translation = json.loads((FLOW.parent / name).read_text(encoding="utf-8"))
    return {
        key
        for flow in ("config", "options")
        for kind in ("abort", "error")
        for key in translation.get(flow, {}).get(kind, {})
    }


@pytest.mark.parametrize("name", TRANSLATION_FILES)
def test_every_message_the_flow_shows_has_a_text(name):
    assert _texts_of(name) == _flow_messages(), (
        f"{name} and the flow disagree about which messages exist"
    )


def _fields_without_help(name: str) -> list[str]:
    """Dialog fields whose label has no help text beside it, or the reverse."""
    translation = json.loads((FLOW.parent / name).read_text(encoding="utf-8"))
    mismatched = []
    for flow in ("config", "options"):
        for step_id, step in translation.get(flow, {}).get("step", {}).items():
            labels = set(step.get("data", {}))
            helps = set(step.get("data_description", {}))
            mismatched += [
                f"{flow}.{step_id}.{field}" for field in sorted(labels ^ helps)
            ]
    return mismatched


@pytest.mark.parametrize("name", TRANSLATION_FILES)
def test_every_dialog_field_explains_itself(name):
    """A field with only a label left the user guessing: what the prefix is
    for, and that it can never be changed, was only in the README."""
    assert not _fields_without_help(name)


def test_the_reader_finds_the_messages_it_is_pointed_at():
    """Proof-of-red for the reader above."""
    messages = _flow_messages()

    assert "already_configured" in messages, "an async_abort reason"
    assert "cannot_connect" in messages, "an errors[...] assignment"
    assert "postfix_required" in messages, "a returned key"
