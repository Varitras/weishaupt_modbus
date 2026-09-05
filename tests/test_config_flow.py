"""The config and reconfigure flows, through Home Assistant's flow manager.

Marked `e2e`: every test boots a Home Assistant core and loads the
integration.
"""

import asyncio

from modbus_connection import ModbusConnectionError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weishaupt_modbus import config_flow
from custom_components.weishaupt_modbus.const import CONF, CONST
from homeassistant.data_entry_flow import FlowResultType

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
    result = await hass.config_entries.flow.async_init(
        CONST.DOMAIN, context={"source": "user"}
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
    refused = await hass.config_entries.flow.async_configure(
        second["flow_id"], {**PAGE_ONE, CONF.HOST: "192.0.2.11"}
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

    assert result["type"] is FlowResultType.ABORT, result.get("errors")
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF.HOST] == "192.0.2.20"
    assert entry.data[CONF.KENNFELD_FILE] == CONST.DEF_KENNFELDFILE
    # The title is the host, and a moved pump that keeps the old one in the
    # integration list is the wrong address in the only place a user looks.
    assert entry.title == "192.0.2.20"


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
