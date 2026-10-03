"""The config and reconfigure flows, through Home Assistant's flow manager.

Marked `e2e`: every test boots a Home Assistant core and loads the
integration.
"""

import asyncio
import json
import logging
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

from modbus_connection import ModbusConnectionError
from probatio import to_field_list
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

import custom_components.weishaupt_modbus as integration
from custom_components.weishaupt_modbus import config_flow
from custom_components.weishaupt_modbus.config_flow import (
    MissingTitles,
    UnclearValues,
    UnknownUnits,
)
from custom_components.weishaupt_modbus.configentry import HOST_LOCKS
from custom_components.weishaupt_modbus.const import CONF, CONST
from custom_components.weishaupt_modbus.webif.client import (
    Broken,
    LoginRefused,
    Unreachable,
)
from custom_components.weishaupt_modbus.webif.discovery import MissingMenuEntries
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import config_validation as cv

from .locking import WatchedLock, until

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(120)]

HOST = "192.0.2.10"

# Prefix and postfix are fixed at creation: the reconfigure page has no field for them.
FIXED_AT_CREATION = (CONF.PREFIX, CONF.DEVICE_POSTFIX)

PAGE_ONE = {
    CONF.HOST: HOST,
    CONF.PORT: 502,
    CONF.PREFIX: CONST.DEF_PREFIX,
    CONF.DEVICE_POSTFIX: "",
    CONF.KENNFELD_FILE: CONST.DEF_KENNFELDFILE,
    CONF.HK2: False,
    CONF.HK3: False,
    CONF.HK4: False,
    CONF.HK5: False,
    CONF.NAME_DEVICE_PREFIX: False,
    CONF.NAME_TOPIC_PREFIX: False,
}


RECONFIGURE_PAGE = {k: v for k, v in PAGE_ONE.items() if k not in FIXED_AT_CREATION}


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    return


@pytest.fixture(autouse=True)
def _pump_that_answers(mock_modbus):
    return mock_modbus


async def _start(hass):
    """The pump's form; with a pump set up already, picked from the menu."""
    result = await hass.config_entries.flow.async_init(
        CONST.DOMAIN, context={"source": "user"}
    )
    if result["type"] is FlowResultType.MENU:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "pump"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def test_the_user_step_creates_an_entry(hass):
    started = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], PAGE_ONE
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == HOST
    assert result["data"][CONF.HOST] == HOST
    assert result["data"][CONF.KENNFELD_FILE] == CONST.DEF_KENNFELDFILE


async def test_short_host_is_rejected(hass):
    started = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], {**PAGE_ONE, CONF.HOST: "ab"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_host"}


async def test_a_host_with_whitespace_inside_is_rejected(hass):
    started = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], {**PAGE_ONE, CONF.HOST: "192.0.2.1 0"}
    )

    assert result["errors"] == {"base": "invalid_host"}


async def test_the_same_pump_cannot_be_set_up_twice(hass):
    """Two entries on one endpoint poll and write the pump twice over.
    The entry's identity is host:port, not the names the user picks."""
    first = await _start(hass)
    created = await hass.config_entries.flow.async_configure(
        first["flow_id"], {**PAGE_ONE, CONF.HOST: " 192.0.2.10 "}
    )
    assert created["type"] is FlowResultType.CREATE_ENTRY
    assert created["data"][CONF.HOST] == "192.0.2.10", "the host was not trimmed"

    second = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        second["flow_id"], {**PAGE_ONE, CONF.HOST: "192.0.2.10"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


def _second_pump(**overrides):
    return {**PAGE_ONE, CONF.HOST: "192.0.2.11", **overrides}


async def _create(hass, page):
    started = await _start(hass)
    return await hass.config_entries.flow.async_configure(started["flow_id"], page)


async def test_a_second_pump_needs_a_postfix_of_its_own(hass):
    """Entity ids are prefix + name + postfix. A second entry with the same
    names loaded with zero entities and said LOADED."""
    assert (await _create(hass, PAGE_ONE))["type"] is FlowResultType.CREATE_ENTRY

    # One flow, corrected in place: a flow that showed an error keeps its
    # unique id, and a second flow on the same pump would abort as in progress.
    started = await _start(hass)
    empty = await hass.config_entries.flow.async_configure(
        started["flow_id"], _second_pump()
    )
    assert empty["errors"] == {"base": "postfix_required"}

    own = await hass.config_entries.flow.async_configure(
        started["flow_id"], _second_pump(**{CONF.DEVICE_POSTFIX: "keller"})
    )
    assert own["type"] is FlowResultType.CREATE_ENTRY

    reused = await _create(
        hass,
        {**_second_pump(**{CONF.DEVICE_POSTFIX: "keller"}), CONF.HOST: "192.0.2.12"},
    )
    assert reused["errors"] == {"base": "postfix_in_use"}


async def _reconfigure(hass, entry, user_input):
    result = await hass.config_entries.flow.async_init(
        CONST.DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def test_a_flow_still_probing_holds_its_postfix_against_a_second_one(
    hass, monkeypatch
):
    """Two dialogs submitted together both passed the namespace check before
    either entry existed, both created an entry with the default empty
    postfix, and one pump loaded with zero entities."""
    probing = asyncio.Event()
    release = asyncio.Event()

    async def held_probe(_hass, _data):
        probing.set()
        await release.wait()
        return True

    monkeypatch.setattr(config_flow, "pump_answers", held_probe)
    first = await hass.config_entries.flow.async_init(
        CONST.DOMAIN, context={"source": "user"}
    )
    first_result = hass.async_create_task(
        hass.config_entries.flow.async_configure(first["flow_id"], dict(PAGE_ONE))
    )
    await asyncio.wait_for(probing.wait(), timeout=5)

    second = await hass.config_entries.flow.async_init(
        CONST.DOMAIN, context={"source": "user"}
    )
    refused = await asyncio.wait_for(
        hass.config_entries.flow.async_configure(
            second["flow_id"], {**PAGE_ONE, CONF.HOST: "192.0.2.11"}
        ),
        timeout=5,
    )
    release.set()
    created = await asyncio.wait_for(first_result, timeout=5)

    assert created["type"] is FlowResultType.CREATE_ENTRY
    assert refused["type"] is FlowResultType.FORM
    assert refused["errors"] == {"base": "postfix_required"}


async def test_reconfigure_updates_the_entry_in_place(hass):
    entry = MockConfigEntry(domain=CONST.DOMAIN, data=PAGE_ONE, version=9)
    entry.add_to_hass(hass)

    result = await _reconfigure(
        hass, entry, {**RECONFIGURE_PAGE, CONF.HOST: "192.0.2.20"}
    )
    # The reconfigure reloads the entry; left running, that reload would end
    # inside Home Assistant's stop at teardown.
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT, result.get("errors")
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF.HOST] == "192.0.2.20"
    assert entry.data[CONF.KENNFELD_FILE] == CONST.DEF_KENNFELDFILE
    # The title is the host, and a moved pump that keeps the old one in the
    # integration list is the wrong address in the only place a user looks.
    assert entry.title == "192.0.2.20"


async def test_reconfigure_refuses_an_address_that_cannot_be_dialled(hass):
    entry = MockConfigEntry(domain=CONST.DOMAIN, data=PAGE_ONE, version=11)
    entry.add_to_hass(hass)

    result = await _reconfigure(hass, entry, {**RECONFIGURE_PAGE, CONF.HOST: "a b"})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_host"}
    assert entry.data[CONF.HOST] == HOST


async def test_reconfigure_to_a_host_without_a_pump_is_reported(hass, mock_modbus):
    """Moving an entry to an address nobody answers on would leave it
    retrying forever; the old address stays until the new one answers."""
    entry = MockConfigEntry(domain=CONST.DOMAIN, data=PAGE_ONE, version=11)
    entry.add_to_hass(hass)
    mock_modbus.fail_requests(ModbusConnectionError("refused"))

    result = await _reconfigure(
        hass, entry, {**RECONFIGURE_PAGE, CONF.HOST: "192.0.2.99"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data[CONF.HOST] == HOST


def test_the_power_map_choice_falls_back_to_the_default_map(tmp_path):
    """An unreadable or empty map folder must still leave something to pick:
    an empty choice list is a form that cannot be submitted."""
    (tmp_path / "empty").mkdir()
    (tmp_path / "maps").mkdir()
    for name in (
        "weishaupt_wsb6_kennfeld.json",
        "weishaupt_wbb_kennfeld.json",
        "x.svg",
    ):
        (tmp_path / "maps" / name).write_text("{}", encoding="utf-8")

    assert config_flow._kennfeld_files(tmp_path / "missing") == [CONST.DEF_KENNFELDFILE]
    assert config_flow._kennfeld_files(tmp_path / "empty") == [CONST.DEF_KENNFELDFILE]
    assert config_flow._kennfeld_files(tmp_path / "maps") == [
        "weishaupt_wbb_kennfeld.json",
        "weishaupt_wsb6_kennfeld.json",
    ]


def test_the_power_map_choice_offers_only_map_files(tmp_path):
    """A folder or a backup whose name merely contained kennfeld.json was
    offered as a map and failed only after it was picked."""
    (tmp_path / "old_kennfeld.json").mkdir()
    (tmp_path / "weishaupt_wbb_kennfeld.json.bak").write_text("{}", encoding="utf-8")
    (tmp_path / "weishaupt_wbb_kennfeld.json").write_text("{}", encoding="utf-8")

    assert config_flow._kennfeld_files(tmp_path) == ["weishaupt_wbb_kennfeld.json"]


async def test_a_host_without_a_pump_is_reported(hass, mock_modbus):
    """A typo in the address used to create an entry that then retried
    forever; the flow now reads one register first."""
    mock_modbus.fail_requests(ModbusConnectionError("refused"))
    started = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], {**PAGE_ONE, CONF.HOST: "192.0.2.99"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_the_probe_waits_while_the_controller_is_busy(hass):
    """The probe read its register while the web interface could be asking
    the same controller: the one access to it that skipped the shared lock."""
    lock = WatchedLock()
    hass.data.setdefault(HOST_LOCKS, {})[HOST] = lock

    async with lock:
        probing = hass.async_create_task(config_flow.pump_answers(hass, PAGE_ONE))
        await until(lambda: lock.waited or probing.done())
        assert not probing.done()

    assert await probing


async def test_prefix_and_postfix_cannot_be_changed_afterwards(hass):
    """Every unique id is built from them: a change orphaned 120 entities'
    history and reset the EEPROM write counters."""
    entry = MockConfigEntry(domain=CONST.DOMAIN, data=PAGE_ONE, version=11)
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        CONST.DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )

    offered = {str(key) for key in result["data_schema"].schema}
    assert not offered & set(FIXED_AT_CREATION), offered
    assert CONF.HOST in offered


async def test_reconfigure_onto_another_entrys_pump_is_refused(hass):
    """Home Assistant only logs a duplicate unique id on update; two entries
    on one endpoint would poll and write the same pump."""
    first = MockConfigEntry(
        domain=CONST.DOMAIN, data=PAGE_ONE, version=11, unique_id="192.0.2.10:502"
    )
    first.add_to_hass(hass)
    second = MockConfigEntry(
        domain=CONST.DOMAIN,
        data={**PAGE_ONE, CONF.HOST: "192.0.2.11", CONF.DEVICE_POSTFIX: "keller"},
        version=11,
        unique_id="192.0.2.11:502",
    )
    second.add_to_hass(hass)

    result = await _reconfigure(
        hass, second, {**RECONFIGURE_PAGE, CONF.HOST: "192.0.2.10"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert second.data[CONF.HOST] == "192.0.2.11", "the entry was changed anyway"


async def test_a_reconfigure_taking_the_endpoint_survives_a_probing_user_flow(
    hass, monkeypatch
):
    """The user flow checked the endpoint before its probe and only the
    postfix after it. A reconfigure moving an existing entry onto that same
    endpoint meanwhile let the flow create its entry, and Home Assistant
    replaced the reconfigured one, taking its entities with it."""
    probing = asyncio.Event()
    release = asyncio.Event()
    probes = 0

    async def held_probe(_hass, _data):
        nonlocal probes
        probes += 1
        if probes > 1:  # the reconfigure below must not wait for itself
            return True
        probing.set()
        await release.wait()
        return True

    monkeypatch.setattr(config_flow, "pump_answers", held_probe)
    existing = MockConfigEntry(
        domain=CONST.DOMAIN,
        data={**PAGE_ONE, CONF.HOST: "192.0.2.30"},
        version=11,
        unique_id="192.0.2.30:502",
    )
    existing.add_to_hass(hass)

    started = await _start(hass)
    pending = hass.async_create_task(
        hass.config_entries.flow.async_configure(
            started["flow_id"],
            {**PAGE_ONE, CONF.HOST: "192.0.2.31", CONF.DEVICE_POSTFIX: "new"},
        )
    )
    await asyncio.wait_for(probing.wait(), timeout=5)

    moved = await _reconfigure(
        hass, existing, {**RECONFIGURE_PAGE, CONF.HOST: "192.0.2.31"}
    )
    assert moved["reason"] == "reconfigure_successful"
    release.set()
    result = await asyncio.wait_for(pending, timeout=5)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_get_entry(existing.entry_id) is not None


# --- a pump's web interface ---------------------------------------------------

WEB_LOGIN = {CONF.USERNAME: "tester", CONF.PASSWORD: "testing"}
FOUND_PAGES = {
    "heat_pump": "/settings_export.html?stack=0C000001000000000080000A0B010002000301",
    "statistics": "/settings_export.html?stack=0C000C23000000000000000A0B020003000401",
    "heating": "/settings_export.html?stack=64001800000000000080000A0B020003000401",
}


def _pump_entry(hass, host=HOST):
    entry = MockConfigEntry(
        domain=CONST.DOMAIN,
        data={**PAGE_ONE, CONF.HOST: host},
        version=11,
        unique_id=f"{host}:502",
        title=host,
    )
    entry.add_to_hass(hass)
    return entry


def _web_entry(hass, pump):
    entry = MockConfigEntry(
        domain=CONST.DOMAIN,
        data={
            CONF.KIND: CONST.WEB_INTERFACE,
            CONF.PUMP_ENTRY: pump.entry_id,
            **WEB_LOGIN,
            CONF.PAGES: FOUND_PAGES,
        },
        version=11,
        unique_id=f"{pump.entry_id}-{CONST.WEB_INTERFACE}",
        title=f"{pump.title} web interface",
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def web_interface(monkeypatch):
    """The short visit to the web interface, scripted: what it finds or raises,
    and, with `held` set, not before the test lets it go.

    The entry a dialog creates is not started here; starting it has tests of
    its own.
    """
    script = SimpleNamespace(visits=[], outcome=FOUND_PAGES, held=None)

    async def visit(_hass, host, user, password):
        script.visits.append((host, user, password))
        if script.held is not None:
            await script.held.wait()
        if isinstance(script.outcome, Exception):
            raise script.outcome
        return script.outcome

    monkeypatch.setattr(config_flow, "read_web_interface", visit)
    monkeypatch.setattr(integration, "async_setup_entry", AsyncMock(return_value=True))
    return script


async def _web_form(hass):
    menu = await hass.config_entries.flow.async_init(
        CONST.DOMAIN, context={"source": "user"}
    )
    assert menu["type"] is FlowResultType.MENU
    form = await hass.config_entries.flow.async_configure(
        menu["flow_id"], {"next_step_id": "webif"}
    )
    assert form["step_id"] == "webif"
    return form


async def test_the_first_pump_is_asked_for_without_a_menu(hass):
    result = await hass.config_entries.flow.async_init(
        CONST.DOMAIN, context={"source": "user"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_with_a_pump_set_up_the_dialog_asks_what_to_add(hass):
    _pump_entry(hass)

    result = await hass.config_entries.flow.async_init(
        CONST.DOMAIN, context={"source": "user"}
    )

    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["pump", "webif"]


async def _submit_while_the_visit_is_held(hass, web_interface, flow_id, user_input):
    """Submit the form; the dialog must answer while the visit still runs."""
    web_interface.held = asyncio.Event()
    shown = await asyncio.wait_for(
        hass.config_entries.flow.async_configure(flow_id, user_input), timeout=5
    )
    web_interface.held.set()
    await hass.async_block_till_done()
    return shown, await hass.config_entries.flow.async_configure(flow_id)


async def test_the_dialog_shows_its_progress_while_it_visits(hass, web_interface):
    """User wish, 2026-10-03: the visit's half minute showed as a spinning
    button only."""
    pump = _pump_entry(hass)
    form = await _web_form(hass)

    shown, result = await _submit_while_the_visit_is_held(
        hass,
        web_interface,
        form["flow_id"],
        {CONF.PUMP_ENTRY: pump.entry_id, **WEB_LOGIN},
    )

    assert shown["type"] is FlowResultType.SHOW_PROGRESS
    assert shown["progress_action"] == "webif_visit"
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_a_reconfigure_shows_its_progress_while_it_visits(hass, web_interface):
    entry = _web_entry(hass, _pump_entry(hass))
    form = await entry.start_reconfigure_flow(hass)

    shown, result = await _submit_while_the_visit_is_held(
        hass,
        web_interface,
        form["flow_id"],
        {CONF.USERNAME: "other", CONF.PASSWORD: "renewed"},
    )
    await hass.async_block_till_done()

    assert shown["type"] is FlowResultType.SHOW_PROGRESS
    assert result["reason"] == "reconfigure_successful"


async def test_a_visit_failing_after_its_progress_shows_the_form_again(
    hass, web_interface
):
    pump = _pump_entry(hass)
    web_interface.outcome = LoginRefused("HTTP 303 to /index.html#wrongpassword")
    form = await _web_form(hass)

    _, result = await _submit_while_the_visit_is_held(
        hass,
        web_interface,
        form["flow_id"],
        {CONF.PUMP_ENTRY: pump.entry_id, **WEB_LOGIN},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    suggested = {
        str(key): key.description["suggested_value"]
        for key in result["data_schema"].schema
        if key.description
    }
    assert suggested == {
        CONF.PUMP_ENTRY: pump.entry_id,
        CONF.USERNAME: WEB_LOGIN[CONF.USERNAME],
    }, "the pump and the user come back, the password not"


async def test_a_pumps_web_interface_is_added_with_the_pages_found(hass, web_interface):
    pump = _pump_entry(hass)
    form = await _web_form(hass)

    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF.PUMP_ENTRY: pump.entry_id, **WEB_LOGIN}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF.KIND: CONST.WEB_INTERFACE,
        CONF.PUMP_ENTRY: pump.entry_id,
        **WEB_LOGIN,
        CONF.PAGES: FOUND_PAGES,
    }
    assert result["result"].unique_id == f"{pump.entry_id}-{CONST.WEB_INTERFACE}"
    assert web_interface.visits == [(HOST, "tester", "testing")]


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (LoginRefused("HTTP 303 to /index.html#wrongpassword"), "invalid_auth"),
        (Unreachable("GET /index.html: TimeoutError"), "cannot_connect"),
        (Broken("/settings_export.html"), "cannot_read"),
    ],
)
async def test_a_visit_that_fails_says_why(hass, web_interface, failure, error):
    pump = _pump_entry(hass)
    web_interface.outcome = failure
    form = await _web_form(hass)

    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF.PUMP_ENTRY: pump.entry_id, **WEB_LOGIN}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_a_controller_in_another_language_is_told_so(hass, web_interface):
    """It said "did not show its menus completely; try again", and trying
    again could never help."""
    pump = _pump_entry(hass)
    web_interface.outcome = MissingMenuEntries({"Statistik", "Heizen"})
    form = await _web_form(hass)

    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF.PUMP_ENTRY: pump.entry_id, **WEB_LOGIN}
    )

    assert result["errors"] == {"base": "missing_menu_entries"}
    assert result["description_placeholders"] == {"titles": "Heizen, Statistik"}


async def test_a_failed_visit_leaves_a_debug_line(hass, web_interface, caplog):
    """The visit's error became a key in the form and nothing else: the log
    could not say why a dialog had failed."""
    caplog.set_level(logging.DEBUG, logger=config_flow.__name__)
    pump = _pump_entry(hass)
    web_interface.outcome = Unreachable("GET /index.html: TimeoutError")
    form = await _web_form(hass)

    await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF.PUMP_ENTRY: pump.entry_id, **WEB_LOGIN}
    )

    assert "GET /index.html: TimeoutError" in caplog.text


@pytest.mark.parametrize(
    ("fault", "error"),
    [
        (MissingTitles, "missing_titles"),
        (UnclearValues, "unclear_values"),
        (UnknownUnits, "unknown_units"),
    ],
)
async def test_a_page_the_dialog_cannot_use_is_named_in_the_form(
    hass, web_interface, fault, error
):
    """Which page, and which of its titles: the only hint a user of another
    model has before the brake would stop every page."""
    pump = _pump_entry(hass)
    web_interface.outcome = fault(
        "Info › Wärmepumpe", frozenset({"EVI Sauggastemperatur", "Verdichter"})
    )
    form = await _web_form(hass)

    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF.PUMP_ENTRY: pump.entry_id, **WEB_LOGIN}
    )

    assert result["errors"] == {"base": error}
    assert result["description_placeholders"] == {
        "page": "Info › Wärmepumpe",
        "titles": "EVI Sauggastemperatur, Verdichter",
    }


async def test_a_reconfigure_names_a_page_it_cannot_use(hass, web_interface):
    entry = _web_entry(hass, _pump_entry(hass))
    web_interface.outcome = MissingTitles("Info › Statistik", frozenset({"JAZ Jahr"}))

    form = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(form["flow_id"], WEB_LOGIN)

    assert result["errors"] == {"base": "missing_titles"}
    assert result["description_placeholders"] == {
        "page": "Info › Statistik",
        "titles": "JAZ Jahr",
    }


def _offered_pumps(form):
    """The pump entries the web interface form lets the user pick."""
    schema = form["data_schema"].schema
    field = next(key for key in schema if str(key) == CONF.PUMP_ENTRY)
    return set(schema[field].container)


async def test_the_web_dialog_offers_only_pumps_without_one(hass, web_interface):
    """A pump that had its web interface was offered, and picking it ended
    the dialog with "This heat pump is already configured"."""
    first = _pump_entry(hass)
    _web_entry(hass, first)
    second = _pump_entry(hass, host="192.0.2.11")

    form = await _web_form(hass)

    assert _offered_pumps(form) == {second.entry_id}


async def test_with_every_pump_on_its_web_interface_the_dialog_says_so(
    hass, web_interface
):
    pump = _pump_entry(hass)
    _web_entry(hass, pump)
    menu = await hass.config_entries.flow.async_init(
        CONST.DOMAIN, context={"source": "user"}
    )

    result = await hass.config_entries.flow.async_configure(
        menu["flow_id"], {"next_step_id": "webif"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "every_pump_has_web_interface"
    assert web_interface.visits == []


async def test_with_the_last_pump_gone_the_dialog_says_there_is_none(
    hass, web_interface
):
    """The menu came with a pump, which went before the web interface was
    picked, and the dialog said every heat pump already had one."""
    pump = _pump_entry(hass)
    menu = await hass.config_entries.flow.async_init(
        CONST.DOMAIN, context={"source": "user"}
    )
    await hass.config_entries.async_remove(pump.entry_id)

    result = await hass.config_entries.flow.async_configure(
        menu["flow_id"], {"next_step_id": "webif"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_pump"


async def test_a_web_interface_added_meanwhile_ends_the_dialog(hass, web_interface):
    """Another dialog added the pump's web interface while this one was open."""
    first = _pump_entry(hass)
    _pump_entry(hass, host="192.0.2.11")
    form = await _web_form(hass)
    _web_entry(hass, first)

    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF.PUMP_ENTRY: first.entry_id, **WEB_LOGIN}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "web_interface_exists"
    assert web_interface.visits == []


async def test_a_refused_login_is_replaced_by_a_new_one(hass, web_interface):
    pump = _pump_entry(hass)
    entry = _web_entry(hass, pump)
    renewed = {CONF.USERNAME: "tester", CONF.PASSWORD: "renewed"}

    form = await entry.start_reauth_flow(hass)
    assert form["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(form["flow_id"], renewed)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert {key: entry.data[key] for key in renewed} == renewed
    assert web_interface.visits == [(HOST, "tester", "renewed")]


@pytest.mark.parametrize("start", ["start_reauth_flow", "start_reconfigure_flow"])
async def test_a_web_interface_whose_pump_was_removed_says_so(
    hass, web_interface, start
):
    """Reauth and reconfigure asked to set the heat pump up first, which could
    not help: a heat pump added again is an entry this one does not know."""
    pump = _pump_entry(hass)
    entry = _web_entry(hass, pump)
    await hass.config_entries.async_remove(pump.entry_id)

    form = await getattr(entry, start)(hass)
    result = await hass.config_entries.flow.async_configure(form["flow_id"], WEB_LOGIN)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "pump_removed"
    assert web_interface.visits == []


async def test_a_web_interface_entry_is_reconfigured_with_a_new_login_and_pages(
    hass, web_interface
):
    """User wish, 2026-10-02: a new login and the pages searched again,
    without removing the entry and its sensors."""
    entry = _web_entry(hass, _pump_entry(hass))
    moved = {
        **FOUND_PAGES,
        "statistics": "/settings_export.html?stack=0C000C28000000000000000A0B020003000401",
    }
    web_interface.outcome = moved
    renewed = {CONF.USERNAME: "other", CONF.PASSWORD: "renewed"}

    form = await entry.start_reconfigure_flow(hass)
    assert form["step_id"] == "reconfigure_webif"
    prefilled = form["data_schema"]({CONF.PASSWORD: "typed"})
    assert prefilled[CONF.USERNAME] == WEB_LOGIN[CONF.USERNAME]
    result = await hass.config_entries.flow.async_configure(form["flow_id"], renewed)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert {key: entry.data[key] for key in renewed} == renewed
    assert entry.data[CONF.PAGES] == moved
    assert web_interface.visits == [(HOST, "other", "renewed")]


async def test_a_reconfigure_visit_that_fails_keeps_the_old_login(hass, web_interface):
    entry = _web_entry(hass, _pump_entry(hass))
    web_interface.outcome = LoginRefused("HTTP 303 to /index.html#wrongpassword")

    form = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF.USERNAME: "other", CONF.PASSWORD: "wrong"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert {key: entry.data[key] for key in WEB_LOGIN} == WEB_LOGIN


async def test_no_login_form_offers_a_password(hass, web_interface):
    """The tests looked at the user name only: a stored or typed password as
    the field's default would go back into the page the browser shows."""
    entry = _web_entry(hass, _pump_entry(hass))
    _pump_entry(hass, host="192.0.2.11")
    forms = [
        await _web_form(hass),
        await entry.start_reauth_flow(hass),
        await entry.start_reconfigure_flow(hass),
    ]

    for form in forms:
        schema = form["data_schema"].schema
        password = next(key for key in schema if str(key) == CONF.PASSWORD)
        assert password.default is vol.UNDEFINED, form["step_id"]
        assert "suggested_value" not in (password.description or {}), form["step_id"]


async def test_no_form_a_failed_visit_shows_again_offers_the_password(
    hass, web_interface
):
    """Only the first form of each dialog was checked: the password just
    typed, as the default of the form shown again, would go back into the
    page the browser shows."""
    entry = _web_entry(hass, _pump_entry(hass))
    other = _pump_entry(hass, host="192.0.2.11")
    web_interface.outcome = LoginRefused("HTTP 303 to /index.html#wrongpassword")
    typed = {CONF.USERNAME: "tester", CONF.PASSWORD: "typed"}
    add = await _web_form(hass)
    reauth = await entry.start_reauth_flow(hass)
    reconfigure = await entry.start_reconfigure_flow(hass)

    forms = [
        await hass.config_entries.flow.async_configure(
            add["flow_id"], {CONF.PUMP_ENTRY: other.entry_id, **typed}
        ),
        await hass.config_entries.flow.async_configure(reauth["flow_id"], typed),
        await hass.config_entries.flow.async_configure(reconfigure["flow_id"], typed),
    ]

    for form in forms:
        assert form["errors"] == {"base": "invalid_auth"}, form["step_id"]
        schema = form["data_schema"].schema
        password = next(key for key in schema if str(key) == CONF.PASSWORD)
        assert password.default is vol.UNDEFINED, form["step_id"]
        assert "suggested_value" not in (password.description or {}), form["step_id"]


async def _loaded(hass, monkeypatch, entry):
    """The entry set up by the scripted setup, and an unload to match: a
    reload would otherwise stop at runtime data the script never made."""
    monkeypatch.setattr(integration, "async_unload_entry", AsyncMock(return_value=True))
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return integration.async_setup_entry.await_count


TRANSLATION_FILES = (
    "strings.json",
    "translations/en.json",
    "translations/de.json",
    "translations/nl.json",
)


async def test_every_field_of_every_form_has_its_label_and_help(hass, web_interface):
    """A renamed option key showed its raw name in the dialog with every test
    passing: the texts were checked against each other, not against the
    forms the flows draw."""
    first_pump = await hass.config_entries.flow.async_init(
        CONST.DOMAIN, context={"source": "user"}
    )
    pump = _pump_entry(hass)
    web = _web_entry(hass, pump)
    _pump_entry(hass, host="192.0.2.11")
    forms = {
        ("config", "user"): first_pump,
        ("config", "webif"): await _web_form(hass),
        ("config", "reauth_confirm"): await web.start_reauth_flow(hass),
        ("config", "reconfigure_webif"): await web.start_reconfigure_flow(hass),
        ("config", "reconfigure"): await pump.start_reconfigure_flow(hass),
        ("options", "webif"): await hass.config_entries.options.async_init(
            web.entry_id
        ),
        ("options", "init"): await hass.config_entries.options.async_init(
            pump.entry_id
        ),
    }
    component = pathlib.Path(config_flow.__file__).parent

    for name in TRANSLATION_FILES:
        texts = json.loads((component / name).read_text(encoding="utf-8"))
        for (flow, step_id), form in forms.items():
            assert form["step_id"] == step_id
            step = texts[flow]["step"][step_id]
            fields = {str(key) for key in form["data_schema"].schema}
            unlabelled = fields - set(step.get("data", {}))
            unexplained = fields - set(step.get("data_description", {}))
            assert not unlabelled, f"{name}: {flow}.{step_id} labels {unlabelled}"
            assert not unexplained, f"{name}: {flow}.{step_id} explains {unexplained}"


async def test_new_options_reload_the_web_interface(hass, web_interface, monkeypatch):
    """The options take effect by a reload; the dialog's test stopped at its
    own answer."""
    entry = _web_entry(hass, _pump_entry(hass))
    started = await _loaded(hass, monkeypatch, entry)

    form = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            CONST.OPTION_WEBIF_HEAT_PUMP_INTERVAL: 10,
            CONST.OPTION_WEBIF_STATISTICS_INTERVAL: 20,
            CONST.OPTION_WEBIF_HEATING_INTERVAL: 30,
        },
    )
    await hass.async_block_till_done()

    assert integration.async_setup_entry.await_count == started + 1


@pytest.mark.parametrize("start", ["start_reauth_flow", "start_reconfigure_flow"])
async def test_a_new_login_reloads_the_web_interface(
    hass, web_interface, monkeypatch, start
):
    entry = _web_entry(hass, _pump_entry(hass))
    started = await _loaded(hass, monkeypatch, entry)

    form = await getattr(entry, start)(hass)
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF.USERNAME: "tester", CONF.PASSWORD: "renewed"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert integration.async_setup_entry.await_count == started + 1


async def test_the_web_interface_interval_is_its_own_option(hass, web_interface):
    entry = _web_entry(hass, _pump_entry(hass))

    chosen = {
        CONST.OPTION_WEBIF_HEAT_PUMP_INTERVAL: 5,
        CONST.OPTION_WEBIF_STATISTICS_INTERVAL: 10,
        CONST.OPTION_WEBIF_HEATING_INTERVAL: 30,
    }

    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert form["step_id"] == "webif"
    assert [str(field) for field in form["data_schema"].schema] == list(chosen)
    for option in chosen:
        with pytest.raises(InvalidData):
            await hass.config_entries.options.async_configure(
                form["flow_id"], {option: 0}
            )
    result = await hass.config_entries.options.async_configure(form["flow_id"], chosen)
    # The options reload the entry; left running, that reload would end
    # inside Home Assistant's stop at teardown.
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == chosen


async def test_the_interval_sliders_show_their_minutes(hass, web_interface):
    """Live, 2026-10-03: a plain range from 1 to 60 drew a slider without its
    value; the minutes showed only while the slider was dragged."""
    entry = _web_entry(hass, _pump_entry(hass))

    form = await hass.config_entries.options.async_init(entry.entry_id)
    # How Home Assistant hands the form to the frontend.
    fields = to_field_list(form["data_schema"], custom_serializer=cv.custom_serializer)

    numbers = [field["selector"]["number"] for field in fields]
    assert [(number["mode"], number["unit_of_measurement"]) for number in numbers] == [
        ("slider", "min")
    ] * 3


async def test_a_web_interface_holds_no_postfix_of_its_own(hass):
    """Only a pump owns a postfix; a web interface left behind by a removed
    pump must not make the next pump need one."""
    pump = _pump_entry(hass, host="192.0.2.99")
    _web_entry(hass, pump)
    await hass.config_entries.async_remove(pump.entry_id)

    assert (await _create(hass, PAGE_ONE))["type"] is FlowResultType.CREATE_ENTRY
