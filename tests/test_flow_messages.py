"""Every message the integration can show has a text, in every language.

The config flow's aborts and errors, and the errors a service call or a
failed poll raises.

A reason or error key without a text renders as the raw key in the dialog.
`unknown` sat in all four files for a year without the flow ever producing
it, while the postfix rules arrived with none of theirs -
two directions of the same drift, and nothing looked at either.
"""

import ast
import json
import pathlib
import re

import pytest

from custom_components.weishaupt_modbus.config_flow import (
    MissingTitles,
    UnclearValues,
    UnknownUnits,
    web_interface_error,
    web_interface_error_placeholders,
)
from custom_components.weishaupt_modbus.webif.client import (
    Broken,
    LoginRefused,
    Unreachable,
)
from custom_components.weishaupt_modbus.webif.discovery import MissingMenuEntries

INTEGRATION = pathlib.Path(__file__).resolve().parents[1] / "custom_components"
FLOW = next(INTEGRATION.glob("*/config_flow.py"))
TRANSLATION_FILES = (
    "strings.json",
    "translations/en.json",
    "translations/de.json",
    "translations/nl.json",
)
# The reasons Home Assistant's own helpers abort a flow with, on its behalf:
# a second dialog for the same unique id showed the raw key.
HELPER_REASONS = {
    "async_set_unique_id": "already_in_progress",
    "_abort_if_unique_id_configured": "already_configured",
}


def _flow_messages() -> set[str]:
    """Every abort reason and error key the flow hands to the frontend.

    Read out of the source rather than listed here: a list beside the flow is
    one more place to forget. The three shapes it uses are
    `async_abort(reason=...)`, an assignment into the `errors` dict, and a
    returned key (`namespace_error`); and the reasons of Home Assistant's
    helpers it calls.
    """
    messages = set()
    helper_reasons = set()
    for node in ast.walk(ast.parse(FLOW.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Attribute) and node.attr in HELPER_REASONS:
            helper_reasons.add(HELPER_REASONS[node.attr])
        if (
            (isinstance(node, ast.keyword) and node.arg == "reason")
            or (
                isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Subscript) for target in node.targets)
            )
            or isinstance(node, ast.Return)
        ):
            messages.add(node.value)
    return helper_reasons | {
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


# What reaches the user as an error message: a service call's refusal or
# failure, the reason a poll failed, and why an entry did not start - shown
# on the entry, where a fixed English sentence once said its pump was gone.
USER_FACING_ERRORS = {
    "HomeAssistantError",
    "ServiceValidationError",
    "UpdateFailed",
    "ConfigEntryError",
    "ConfigEntryNotReady",
    "ConfigEntryAuthFailed",
}


def _called_name(func: ast.expr) -> str | None:
    """`HomeAssistantError(...)` and `exceptions.HomeAssistantError(...)` alike."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _raised_errors() -> tuple[list[str], dict[str, set[str]]]:
    """Raises of a user-facing error without a translation key, and the
    placeholders each translation key is raised with."""
    untranslated = []
    placeholders: dict[str, set[str]] = {}
    for module in sorted(FLOW.parent.rglob("*.py")):
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if not (
                isinstance(node, ast.Raise)
                and isinstance(node.exc, ast.Call)
                and _called_name(node.exc.func) in USER_FACING_ERRORS
            ):
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.exc.keywords}
            key = keywords.get("translation_key")
            if not isinstance(key, ast.Constant):
                untranslated.append(f"{module.name}:{node.lineno}")
                continue
            given = keywords.get("translation_placeholders")
            placeholders[key.value] = (
                {k.value for k in given.keys} if isinstance(given, ast.Dict) else set()
            )
    return untranslated, placeholders


def test_every_error_the_user_sees_is_translated():
    """A fixed English sentence reached every user, whatever their language."""
    untranslated, _ = _raised_errors()
    assert not untranslated, f"raised without a translation_key: {untranslated}"


@pytest.mark.parametrize("name", TRANSLATION_FILES)
def test_every_translated_error_has_its_text_and_placeholders(name):
    _, raised = _raised_errors()
    texts = json.loads((FLOW.parent / name).read_text(encoding="utf-8"))
    messages = {
        key: entry["message"] for key, entry in texts.get("exceptions", {}).items()
    }

    assert set(messages) == set(raised), f"{name} and the code disagree"
    for key, given in raised.items():
        used = set(re.findall(r"\{(\w+)\}", messages[key]))
        assert used == given, f"{name}: {key} uses {used}, the code gives {given}"


# One of each way a dialog's visit can fail, as the form gets it.
VISIT_FAILURES = (
    LoginRefused("HTTP 303 to /index.html#wrongpassword"),
    Unreachable("GET /index.html: TimeoutError"),
    Broken("/settings_export.html"),
    MissingTitles("Info › Wärmepumpe", frozenset({"Hochdruck"})),
    UnclearValues("Info › Wärmepumpe", frozenset({"Hochdruck"})),
    UnknownUnits("Info › Wärmepumpe", frozenset({"Hochdruck"})),
    MissingMenuEntries({"Statistik"}),
)


def _keys_returned_by(function: str) -> set[str]:
    tree = ast.parse(FLOW.read_text(encoding="utf-8"))
    body = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == function
    )
    return {
        node.value.value
        for node in ast.walk(body)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)
    }


@pytest.mark.parametrize("name", TRANSLATION_FILES)
def test_every_visit_error_names_what_the_code_fills_in(name):
    """A translation that dropped {titles} named nothing, and the placeholder
    check covered the raised errors only, not the dialog's."""
    errors = json.loads((FLOW.parent / name).read_text(encoding="utf-8"))["config"][
        "error"
    ]

    assert {web_interface_error(failure) for failure in VISIT_FAILURES} == (
        _keys_returned_by("web_interface_error")
    ), "a visit error without a failure above to check it by"
    for failure in VISIT_FAILURES:
        key = web_interface_error(failure)
        used = set(re.findall(r"\{(\w+)\}", errors[key]))
        given = set(web_interface_error_placeholders(failure))
        assert used == given, f"{name}: {key} uses {used}, the code gives {given}"


def test_every_icon_belongs_to_an_entity_that_exists():
    icons = json.loads((FLOW.parent / "icons.json").read_text(encoding="utf-8"))
    strings = json.loads((FLOW.parent / "strings.json").read_text(encoding="utf-8"))

    stale = [
        f"{platform}.{key}"
        for platform, keys in icons["entity"].items()
        for key in keys
        if key not in strings["entity"].get(platform, {})
    ]
    assert not stale, f"icons for entities that do not exist: {stale}"


def test_the_reader_finds_the_messages_it_is_pointed_at():
    """Proof-of-red for the reader above."""
    messages = _flow_messages()

    assert "already_configured" in messages, "an async_abort reason"
    assert "cannot_connect" in messages, "an errors[...] assignment"
    assert "postfix_required" in messages, "a returned key"
    assert "already_in_progress" in messages, "a reason of a helper it calls"
