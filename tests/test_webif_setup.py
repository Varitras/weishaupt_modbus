"""Starting and stopping a pump's web interface entry, against the stand-in.

Marked `e2e`: every test boots a Home Assistant core and loads the
integration.
"""

import asyncio
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weishaupt_modbus.configentry import host_lock
from custom_components.weishaupt_modbus.const import CONF, CONST
from custom_components.weishaupt_modbus.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.weishaupt_modbus.webif import client as webif
from custom_components.weishaupt_modbus.webif.discovery import (
    HEAT_PUMP_PAGE,
    HEATING_PAGE,
    STATISTICS_PAGE,
)
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState

from .webif_stand_in import (
    HEAT_PUMP_INFO,
    HEATING,
    INFO,
    PASSWORD,
    PUMP_MENU,
    STACK,
    STATISTICS_INFO,
    USER,
    StandInPump,
    column,
    link,
    serving,
    value,
)

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(120)]

PAGES = {
    HEAT_PUMP_PAGE: STACK + f"{INFO},{HEAT_PUMP_INFO}",
    STATISTICS_PAGE: STACK + f"{INFO},{STATISTICS_INFO}",
    HEATING_PAGE: STACK + f"{PUMP_MENU},{HEATING}",
}
SWITCHING_DIFFERENCE = "64001805000000002D40000A0B030011010401"
POWER_LIMIT = "64001807000000003C40000A0B030011010401"
SITE = {
    PAGES[HEAT_PUMP_PAGE]: column(
        value("Betrieb", "Heizbetrieb") + value("Hochdruck", "24.4 BAR")
    ),
    PAGES[STATISTICS_PAGE]: column(
        value("JAZ Jahr", "4.16") + value("JAZ gesamt", "4.20")
    ),
    PAGES[HEATING_PAGE]: column(
        link([PUMP_MENU, HEATING, SWITCHING_DIFFERENCE], "Schaltdifferenz", "4.5 K")
        + link([PUMP_MENU, HEATING, POWER_LIMIT], "Leistungsbegrenzung", "60 %")
    ),
}
LOGIN = [("GET", webif.INDEX), ("POST", webif.LOGIN)]
REDACTED = "**REDACTED**"


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    return


@pytest.fixture
async def pump(socket_enabled, monkeypatch):
    """The stand-in answering the three pages; the entry asks without pauses."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    stand_in = StandInPump()
    stand_in.site = dict(SITE)
    async with serving(stand_in) as served:
        yield served


def _entries(hass, pump, password=PASSWORD, options=None):
    """A pump entry and its web interface; the pump's Modbus side is not under
    test here, so its entry stays disabled."""
    pump_entry = MockConfigEntry(
        domain=CONST.DOMAIN,
        data={CONF.HOST: pump.host},
        version=11,
        disabled_by=ConfigEntryDisabler.USER,
        title="pump",
    )
    pump_entry.add_to_hass(hass)
    web = MockConfigEntry(
        domain=CONST.DOMAIN,
        data={
            CONF.KIND: CONST.WEB_INTERFACE,
            CONF.PUMP_ENTRY: pump_entry.entry_id,
            CONF.USERNAME: USER,
            CONF.PASSWORD: password,
            CONF.PAGES: PAGES,
        },
        options=options or {},
        version=11,
        title="pump web interface",
    )
    web.add_to_hass(hass)
    return web


async def _start(hass, entry):
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_a_web_interface_starts_with_a_round_of_its_pages(hass, pump):
    entry = await _start(hass, _entries(hass, pump))

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator.data == {
        HEAT_PUMP_PAGE: {"Betrieb": "Heizbetrieb", "Hochdruck": "24.4 BAR"},
        STATISTICS_PAGE: {"JAZ Jahr": "4.16", "JAZ gesamt": "4.20"},
        HEATING_PAGE: {"Schaltdifferenz": "4.5 K", "Leistungsbegrenzung": "60 %"},
    }
    assert pump.asked == [*LOGIN, *(("GET", path) for path in PAGES.values())]


async def test_unloading_stops_and_logs_out(hass, pump):
    entry = await _start(hass, _entries(hass, pump))

    assert await hass.config_entries.async_unload(entry.entry_id)

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert pump.asked[-1] == ("GET", webif.LOGOUT)


async def test_a_refused_login_asks_for_a_new_one(hass, pump):
    entry = await _start(hass, _entries(hass, pump, password="outdated"))

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(CONST.DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]
    assert pump.asked == LOGIN


async def test_a_web_interface_without_its_pump_does_not_start(hass, pump):
    entry = _entries(hass, pump)
    await hass.config_entries.async_remove(entry.data[CONF.PUMP_ENTRY])

    await _start(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert "removed" in entry.reason
    assert pump.asked == []


async def test_the_heat_pump_page_follows_the_interval_option(hass, pump):
    entry = await _start(
        hass, _entries(hass, pump, options={CONST.OPTION_WEBIF_INTERVAL: 5})
    )

    assert entry.runtime_data.coordinator.update_interval == timedelta(minutes=5)


async def test_the_web_interface_waits_while_its_pump_is_asked(hass, pump):
    """Modbus and the web interface of one pump never ask it at once."""
    entry = _entries(hass, pump)

    async with host_lock(hass, pump.host):
        starting = asyncio.create_task(hass.config_entries.async_setup(entry.entry_id))
        await asyncio.sleep(0.1)
        assert pump.asked == []
    await starting
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED


async def test_the_diagnostics_leave_out_the_login_and_the_page_addresses(hass, pump):
    entry = await _start(hass, _entries(hass, pump))

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    data = diagnostics["entry"]["data"]
    assert {data[CONF.USERNAME], data[CONF.PASSWORD], data[CONF.PAGES]} == {REDACTED}
    assert PAGES[HEATING_PAGE] not in str(diagnostics)
    assert diagnostics["coordinator"]["pages"][STATISTICS_PAGE] == {
        "JAZ Jahr": "4.16",
        "JAZ gesamt": "4.20",
    }
