"""The config and reconfigure flows, through Home Assistant's flow manager.

Marked `e2e`: every test boots a Home Assistant core and loads the
integration.
"""

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
    CONF.CB_WEBIF: False,
}

WEBIF_PAGE = {
    CONF.CB_WEBIF_MOCKUP_DATA: False,
    CONF.USERNAME: "user",
    CONF.PASSWORD: "secret",
    CONF.WEBIF_TOKEN: "token",
    CONF.CB_WEBIF_HK1: True,
    CONF.CB_WEBIF_HK2: False,
    CONF.CB_WEBIF_HK3: False,
    CONF.CB_WEBIF_HK4: False,
    CONF.CB_WEBIF_HK5: False,
    CONF.CB_WEBIF_WP: False,
    CONF.CB_WEBIF_2WEZ: False,
    CONF.CB_WEBIF_SATISTICS: False,
}


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    return


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


async def test_webif_checkbox_leads_to_second_step(hass):
    started = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], {**PAGE_ONE, CONF.CB_WEBIF: True}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "webif"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], WEBIF_PAGE
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF.USERNAME] == "user"
    assert result["data"][CONF.CB_WEBIF_HK1] is True


async def _reconfigure(hass, entry, user_input):
    result = await hass.config_entries.flow.async_init(
        CONST.DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def test_reconfigure_drops_stale_webif_settings_when_unticked(hass):
    """Credentials left behind after the WebIF is switched off would be
    picked up again by the next setup that sees them."""
    entry = MockConfigEntry(
        domain=CONST.DOMAIN,
        data={**PAGE_ONE, CONF.CB_WEBIF: True, **WEBIF_PAGE},
        version=8,
    )
    entry.add_to_hass(hass)

    result = await _reconfigure(hass, entry, {**PAGE_ONE, CONF.CB_WEBIF: False})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert CONF.USERNAME not in entry.data
    assert CONF.CB_WEBIF_HK1 not in entry.data
    assert entry.data[CONF.HOST] == HOST


async def test_reconfigure_with_webif_keeps_going_to_the_second_step(hass):
    entry = MockConfigEntry(domain=CONST.DOMAIN, data=PAGE_ONE, version=8)
    entry.add_to_hass(hass)

    result = await _reconfigure(hass, entry, {**PAGE_ONE, CONF.CB_WEBIF: True})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "webif"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], WEBIF_PAGE
    )

    assert result["type"] is FlowResultType.ABORT
    assert entry.data[CONF.USERNAME] == "user"
