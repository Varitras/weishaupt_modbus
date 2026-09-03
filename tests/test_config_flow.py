"""The config and reconfigure flows, through Home Assistant's flow manager.

Marked `e2e`: every test boots a Home Assistant core and loads the
integration.
"""

from modbus_connection import ModbusConnectionError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weishaupt_modbus.const import CONF, CONST
from homeassistant.data_entry_flow import FlowResultType

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(120)]

HOST = "192.0.2.10"

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


async def _reconfigure(hass, entry, user_input):
    result = await hass.config_entries.flow.async_init(
        CONST.DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def test_reconfigure_updates_the_entry_in_place(hass):
    entry = MockConfigEntry(domain=CONST.DOMAIN, data=PAGE_ONE, version=9)
    entry.add_to_hass(hass)

    result = await _reconfigure(hass, entry, {**PAGE_ONE, CONF.HOST: "192.0.2.20"})

    assert result["type"] is FlowResultType.ABORT, result.get("errors")
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF.HOST] == "192.0.2.20"
    assert entry.data[CONF.KENNFELD_FILE] == CONST.DEF_KENNFELDFILE


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
