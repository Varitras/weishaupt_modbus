"""End-to-end tests against a real Home Assistant instance.

These exercise the parts that only exist once Home Assistant itself is driving
the integration: entry setup and unload, the config-entry migration, and the
entity platforms. Everything below the coordinator (the Modbus wire) is faked -
the point here is the Home Assistant contract, not the client.

Marked `e2e` because each test boots a full Home Assistant instance; the
everyday run deselects them, CI runs them with `-m ""`.
"""

import asyncio
from datetime import timedelta
import json
import logging
import pathlib

from modbus_connection import (
    GatewayTargetError,
    IllegalDataAddressError,
    ModbusConnectionError,
    ModbusTimeoutError,
    ServerDeviceBusyError,
    ServerDeviceFailureError,
)
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.weishaupt_modbus.configentry import host_lock
from custom_components.weishaupt_modbus.const import CONF, CONST, DeviceConstants
from custom_components.weishaupt_modbus.weishaupt_modbus_api.const import DEFAULT_PORT
from custom_components.weishaupt_modbus.weishaupt_modbus_api.device import (
    WeishauptHeatPump,
)
from custom_components.weishaupt_modbus.weishaupt_modbus_api.exceptions import (
    WriteError,
)
from custom_components.weishaupt_modbus.weishaupt_modbus_api.hpconst import DEVICELISTS
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EntityCategory
from homeassistant.core import State
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.restore_state import async_get as async_get_restored
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(120)]

INTEGRATION = (
    pathlib.Path(__file__).resolve().parents[1] / "custom_components" / CONST.DOMAIN
)

OUTSIDE_TEMPERATURE = 30001
OUTSIDE_TEMPERATURE_UNIQUE_ID = "weishaupt_wbbAussentemperatur"
SYSTEM_OPERATION_MODE = 40001
SYSTEM_OPERATION_MODE_UNIQUE_ID = "weishaupt_wbbSystembetriebsart"
COMFORT_ROOM_TEMPERATURE = 41105
COMFORT_ROOM_TEMPERATURE_UNIQUE_ID = "weishaupt_wbbRaumsolltemperatur Komfort"
SUMMER = 3
ELECTRICAL_POWER = 33126
ELECTRICAL_POWER_UNIQUE_ID = "weishaupt_wbbElektrische Leistungsaufnahme"
FLOW_TEMPERATURE = 33104
RETURN_TEMPERATURE = 33105
SPREAD_UNIQUE_ID = "weishaupt_wbbSpreizung"

BASE_DATA = {
    CONF.HOST: "192.0.2.10",
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
def pump(mock_modbus):
    """A pump that answers 12.3 °C outside on the shared in-memory connection."""
    mock_modbus.load_raw({"input": {OUTSIDE_TEMPERATURE: 123}})
    return mock_modbus


def _entry(hass, data=None, version=11):
    entry = MockConfigEntry(
        domain=CONST.DOMAIN, title="pump", data=data or BASE_DATA, version=version
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _reconfigure(hass, entry, changes):
    """What reconfigure does: new data, then a reload."""
    hass.config_entries.async_update_entry(entry, data={**entry.data, **changes})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def test_the_pump_polls_and_writes_under_the_lock_its_web_interface_shares(
    hass,
):
    """Handed a lock of its own, the pump would poll and write beside a
    request of its web interface, and nothing showed it."""
    entry = await _setup(hass, _entry(hass))

    device = entry.runtime_data.coordinator.device
    assert device._host_lock is host_lock(hass, entry.data[CONF.HOST])


async def test_setup_creates_a_sensor_from_the_first_refresh(hass):
    entry = await _setup(hass, _entry(hass))

    assert entry.state is ConfigEntryState.LOADED
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", CONST.DOMAIN, OUTSIDE_TEMPERATURE_UNIQUE_ID
    )
    assert entity_id, "the outside temperature got no entity"
    # From the FIRST refresh: the listener only fires on the next poll, and
    # every entity read unknown for a whole scan interval after setup.
    assert hass.states.get(entity_id).state == "12.3"


async def test_a_calculated_sensor_starts_from_the_first_refresh(hass, pump):
    """The constructor computes the start value. A second computation when
    the entity was added hid, for calculated sensors only, whether the
    first one worked at all."""
    pump.load_raw({"input": {FLOW_TEMPERATURE: 350, RETURN_TEMPERATURE: 300}})
    await _setup(hass, _entry(hass))
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", CONST.DOMAIN, SPREAD_UNIQUE_ID
    )

    assert entity_id, "the spread got no entity"
    assert float(hass.states.get(entity_id).state) == 5.0


async def test_temperature_differences_are_not_converted_as_temperatures(hass, pump):
    """Declared as temperatures, a 5 K spread read 41 degF in US units and
    278.15 K to whoever chose kelvin, and the SG-Ready boost offered 32 to
    86 degF and refused a raise of 5. A difference keeps its unit unless the
    user picks another, which then converts it as a difference."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    pump.load_raw(
        {
            "input": {FLOW_TEMPERATURE: 350, RETURN_TEMPERATURE: 300},
            "holding": {SG_READY_BOOST: 0},
        }
    )
    await _setup(hass, _entry(hass))
    registry = er.async_get(hass)
    spread = registry.async_get_entity_id("sensor", CONST.DOMAIN, SPREAD_UNIQUE_ID)
    boost = registry.async_get_entity_id(
        "number", CONST.DOMAIN, SG_READY_BOOST_UNIQUE_ID
    )
    writes = []
    pump.unit.on_write(writes.append)

    await hass.services.async_call(
        "number", "set_value", {"entity_id": boost, "value": 5}, blocking=True
    )

    shown = hass.states.get(spread)
    assert (shown.state, shown.attributes["unit_of_measurement"]) == ("5.0", "°C")
    assert hass.states.get(boost).attributes["max"] == 30.0
    assert [(event.address, event.values) for event in writes] == [
        (SG_READY_BOOST, [50])
    ]
    registry.async_update_entity_options(
        spread, "sensor", {"unit_of_measurement": "°F"}
    )
    await hass.async_block_till_done()
    assert float(hass.states.get(spread).state) == 9.0


async def test_the_configured_port_reaches_the_client(hass, mock_modbus):
    """The port was stored by the config flow and never passed on: a pump on
    any port but 502 could not be reached with a valid configuration."""
    await _setup(hass, _entry(hass, data={**BASE_DATA, CONF.PORT: 5020}))

    assert mock_modbus.params_seen[0].port == 5020


async def test_a_register_without_a_sensor_is_unavailable_not_unknown(hass, pump):
    """Decision E1: 0x8000 means nothing is connected, and that is
    unavailable - not an unknown reading of a sensor that exists."""
    pump.load_raw({"input": {OUTSIDE_TEMPERATURE: 0x8000}})
    entry = await _setup(hass, _entry(hass))
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", CONST.DOMAIN, OUTSIDE_TEMPERATURE_UNIQUE_ID
    )
    assert hass.states.get(entity_id).state == "unavailable"

    pump.load_raw({"input": {OUTSIDE_TEMPERATURE: 123}})
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "12.3"


async def test_a_second_poll_reaches_every_platform(hass, pump):
    """Every entity followed the poll only through its coordinator listener,
    and nothing exercised that listener: the callbacks could be emptied and
    the suite stayed green, while every entity in Home Assistant would keep
    the reading of its first refresh forever."""
    pump.load_raw(
        {
            "holding": {
                SYSTEM_OPERATION_MODE: 0,
                COMFORT_ROOM_TEMPERATURE: 215,
            }
        }
    )
    entry = await _setup(hass, _entry(hass))
    registry = er.async_get(hass)
    entity_ids = {
        platform: registry.async_get_entity_id(platform, CONST.DOMAIN, unique_id)
        for platform, unique_id in (
            ("sensor", OUTSIDE_TEMPERATURE_UNIQUE_ID),
            ("select", SYSTEM_OPERATION_MODE_UNIQUE_ID),
            ("number", COMFORT_ROOM_TEMPERATURE_UNIQUE_ID),
        )
    }
    assert hass.states.get(entity_ids["select"]).state == "sys_operationmode_automatic"

    pump.load_raw(
        {
            "input": {OUTSIDE_TEMPERATURE: 456},
            "holding": {
                SYSTEM_OPERATION_MODE: SUMMER,
                COMFORT_ROOM_TEMPERATURE: 230,
            },
        }
    )
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(entity_ids["sensor"]).state == "45.6"
    assert hass.states.get(entity_ids["select"]).state == "sys_operationmode_summer"
    assert hass.states.get(entity_ids["number"]).state == "23.0"


# The setpoints the controller only reports, per the register reference: room
# and flow for each circuit, and hot water.
REPORTED_SETPOINTS = {
    *(31101 + 100 * circuit for circuit in range(5)),
    *(31104 + 100 * circuit for circuit in range(5)),
    32101,
}
ALL_CIRCUITS = {
    **BASE_DATA,
    CONF.HK2: True,
    CONF.HK3: True,
    CONF.HK4: True,
    CONF.HK5: True,
}


@pytest.mark.parametrize("no_demand", [1, 0x8000])
async def test_every_reported_setpoint_with_no_demand_reads_zero_in_home_assistant(
    hass, pump, no_demand
):
    """Live, the controller reported 1 on 31104 all summer. Held by that row
    alone, hot water, the room setpoints and circuits 2-5 could go back to
    unknown unnoticed."""
    rows = [
        row for device in DEVICELISTS for row in device if row.params.get("setpoint")
    ]
    assert {row.address for row in rows} == REPORTED_SETPOINTS
    pump.load_raw({"input": dict.fromkeys(REPORTED_SETPOINTS, no_demand)})
    await _setup(hass, _entry(hass, data=ALL_CIRCUITS))
    registry = er.async_get(hass)

    for row in rows:
        entity_id = registry.async_get_entity_id(
            "sensor", CONST.DOMAIN, CONST.DEF_PREFIX + row.name
        )
        state = hass.states.get(entity_id)
        assert (state.state, state.attributes.get("demand")) == ("0.0", "none"), row


SG_READY_BOOST = 42105
SG_READY_BOOST_UNIQUE_ID = "weishaupt_wbbSG Ready Anhebung"


async def test_a_switched_off_setpoint_has_a_switch_and_an_unknown_number(hass, pump):
    """Live: the SG-Ready boost reads 0x8000, which the controller's menu
    calls off. As a missing sensor it was unavailable and could not be
    turned on again from Home Assistant."""
    pump.load_raw({"holding": {SG_READY_BOOST: 0x8000}})
    entry = await _setup(hass, _entry(hass))
    registry = er.async_get(hass)
    number_id = registry.async_get_entity_id(
        "number", CONST.DOMAIN, SG_READY_BOOST_UNIQUE_ID
    )
    switch_id = registry.async_get_entity_id(
        "switch", CONST.DOMAIN, SG_READY_BOOST_UNIQUE_ID + "_active"
    )
    assert hass.states.get(number_id).state == "unknown"
    assert hass.states.get(switch_id).state == "off"

    writes = []
    pump.unit.on_write(writes.append)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert [(event.address, event.values) for event in writes] == [
        (SG_READY_BOOST, [0])
    ]
    assert hass.states.get(switch_id).state == "on"
    assert entry.runtime_data.coordinator.device.write_budget.total == 1


async def test_a_tolerated_failed_poll_keeps_the_published_values(hass, pump):
    """The grace returned the old dictionary while the rows the entities read
    were already half rewritten: a refused system band made the outside
    temperature unavailable at once, and a later link error published the
    new outside value with everything else a poll old."""
    entry = await _setup(hass, _entry(hass))
    outside = er.async_get(hass).async_get_entity_id(
        "sensor", CONST.DOMAIN, OUTSIDE_TEMPERATURE_UNIQUE_ID
    )
    coordinator = entry.runtime_data.coordinator

    pump.fail_read_band(30001)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success is True
    assert hass.states.get(outside).state == "12.3"

    pump.unit.fail_read(30001, None, register_type="input")
    pump.load_raw({"input": {OUTSIDE_TEMPERATURE: 234}})
    pump.unit.fail_read(
        33101, ModbusConnectionError("later band timed out"), register_type="input"
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is True
    assert hass.states.get(outside).state == "12.3", "a half-read poll must not show"


def _integration_warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("custom_components.weishaupt_modbus")
        and record.levelno >= logging.WARNING
    ]


async def test_an_outage_is_logged_once_when_it_starts_and_once_when_it_ends(
    hass, pump, caplog
):
    """Every tolerated failure logged a warning: a blip that healed itself
    left three warnings, an outage three plus the error."""
    caplog.set_level(logging.INFO)
    entry = await _setup(hass, _entry(hass))
    coordinator = entry.runtime_data.coordinator
    pump.fail_requests(ModbusConnectionError("link down"))

    for _ in range(3):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert not _integration_warnings(caplog), "a tolerated failure is not news"

    await coordinator.async_refresh()
    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    assert len(_integration_warnings(caplog)) == 1

    pump.fail_requests(None)
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert "recovered" in caplog.text


async def test_the_number_and_its_switch_agree_right_after_a_write(hass, pump):
    """Each cached its own state: setting the number left the switch off,
    switching off left the number at 5.0, until the next poll."""
    pump.load_raw({"holding": {SG_READY_BOOST: 0x8000}})
    await _setup(hass, _entry(hass))
    registry = er.async_get(hass)
    number = registry.async_get_entity_id(
        "number", CONST.DOMAIN, SG_READY_BOOST_UNIQUE_ID
    )
    switch = registry.async_get_entity_id(
        "switch", CONST.DOMAIN, SG_READY_BOOST_UNIQUE_ID + "_active"
    )

    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 5}, blocking=True
    )
    assert hass.states.get(number).state == "5.0"
    assert hass.states.get(switch).state == "on"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": switch}, blocking=True
    )
    assert hass.states.get(switch).state == "off"
    assert hass.states.get(number).state == "unknown"


def _boost_switch(hass):
    return er.async_get(hass).async_get_entity_id(
        "switch", CONST.DOMAIN, SG_READY_BOOST_UNIQUE_ID + "_active"
    )


async def _switch(hass, service, entity_id):
    await hass.services.async_call(
        "switch", service, {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()


async def test_a_setpoint_switched_off_before_a_reload_comes_back_with_its_value(
    hass, pump
):
    """The value it held lived in memory only: after a reload, turning the
    setpoint on wrote the range's minimum - for the summer/winter switchover
    3 degC, which keeps the heating off all winter."""
    pump.load_raw({"holding": {SG_READY_BOOST: 50}})
    entry = await _setup(hass, _entry(hass))
    await _switch(hass, "turn_off", _boost_switch(hass))
    # The stand-in hands each load a fresh connection from what it was given.
    pump.load_raw({"holding": {SG_READY_BOOST: 0x8000}})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    writes = []
    pump.unit.on_write(writes.append)

    await _switch(hass, "turn_on", _boost_switch(hass))

    assert [(event.address, event.values) for event in writes] == [
        (SG_READY_BOOST, [50])
    ]


@pytest.mark.parametrize(
    ("stored", "written"),
    [({"last_setting": 50}, 50), ({"last_setting": "50"}, 0), ({}, 0)],
    ids=["kept", "not a register value", "none kept"],
)
async def test_a_setpoint_off_at_a_restart_comes_back_with_the_value_it_held(
    hass, pump, stored, written
):
    """The same across a restart; a stored value that is not a register
    value is no value, and the range's minimum stays the fallback."""
    pump.load_raw({"holding": {SG_READY_BOOST: 0x8000}})
    entry = await _setup(hass, _entry(hass))
    switch = _boost_switch(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    mock_restore_cache_with_extra_data(hass, [(State(switch, "off"), stored)])
    await _setup(hass, entry)
    writes = []
    pump.unit.on_write(writes.append)

    await _switch(hass, "turn_on", switch)

    assert [(event.address, event.values) for event in writes] == [
        (SG_READY_BOOST, [written])
    ]


async def test_a_setpoint_read_at_the_start_wins_over_the_one_kept(hass, pump):
    """What the controller shows now is newer than what was kept."""
    pump.load_raw({"holding": {SG_READY_BOOST: 70}})
    entry = await _setup(hass, _entry(hass))
    switch = _boost_switch(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    mock_restore_cache_with_extra_data(
        hass, [(State(switch, "on"), {"last_setting": 50})]
    )
    await _setup(hass, entry)
    await _switch(hass, "turn_off", switch)
    writes = []
    pump.unit.on_write(writes.append)

    await _switch(hass, "turn_on", switch)

    assert [(event.address, event.values) for event in writes] == [
        (SG_READY_BOOST, [70])
    ]


DHW_NORMAL = 42103
DHW_LOWERING = 42104


async def test_dhw_setpoints_keep_their_own_floor_and_the_latest_neighbour(hass, pump):
    """15 degC reached register 42103 although normal starts at 20, and 50 degC
    reached 42104 right after normal had been lowered to 40."""
    pump.load_raw({"holding": {DHW_NORMAL: 600, DHW_LOWERING: 100}})
    await _setup(hass, _entry(hass))
    registry = er.async_get(hass)
    normal = registry.async_get_entity_id(
        "number", CONST.DOMAIN, "weishaupt_wbbWarmwasser Normal"
    )
    lowering = registry.async_get_entity_id(
        "number", CONST.DOMAIN, "weishaupt_wbbWarmwasser Absenk"
    )
    assert hass.states.get(normal).attributes["min"] == 20
    writes = []
    pump.unit.on_write(writes.append)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": normal, "value": 15}, blocking=True
        )
    await hass.services.async_call(
        "number", "set_value", {"entity_id": normal, "value": 40}, blocking=True
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": lowering, "value": 50}, blocking=True
        )

    assert [(event.address, event.values) for event in writes] == [(DHW_NORMAL, [400])]


@pytest.mark.parametrize("version", [2, 9, 10])
async def test_a_routine_migration_and_setup_warn_of_nothing(
    hass, pump, caplog, version
):
    """Up to seven warnings told of a migration that asks nothing of the
    user, and every setup ended in "Init done" at info."""
    with caplog.at_level(logging.INFO, logger="custom_components.weishaupt_modbus"):
        await _setup(hass, _entry(hass, version=version))

    ours = [
        record
        for record in caplog.records
        if record.name.startswith("custom_components.weishaupt_modbus")
    ]
    assert not [record for record in ours if record.levelno >= logging.WARNING]
    assert "Init done" not in [record.getMessage() for record in ours]


async def test_a_taken_entity_id_does_not_stop_the_relabel_migration(hass, pump):
    """An unrelated entity already owned the id the relabel wanted; the
    registry raised and the entry ended in MIGRATION_ERROR, retry after retry."""
    entry = _entry(hass, version=9)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor",
        CONST.DOMAIN,
        "weishaupt_wbbBetriebsstunden E1",
        config_entry=entry,
        suggested_object_id="wh_2nd_heat_source_operation_hours_e1",
    )
    occupied = registry.async_get_or_create(
        "sensor",
        "test",
        "independent-device",
        suggested_object_id="wh_2nd_heat_source_switching_cycles_2nd_heat_source",
    )

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 11
    kept = registry.async_get(old.entity_id)
    assert kept is not None, "the old id stays when the new one is taken"
    assert kept.unique_id == "weishaupt_wbbSchaltspiele 2. WEZ"
    assert registry.async_get(occupied.entity_id).unique_id == "independent-device"


async def test_a_refused_band_leaves_its_entities_unavailable(hass, pump):
    entry = _entry(hass)
    pump.fail_read_band(34101)
    await _setup(hass, entry)
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", CONST.DOMAIN, "weishaupt_wbbStatus 2. WEZ"
    )

    assert hass.states.get(entity_id).state == "unavailable"
    outside = er.async_get(hass).async_get_entity_id(
        "sensor", CONST.DOMAIN, OUTSIDE_TEMPERATURE_UNIQUE_ID
    )
    assert hass.states.get(outside).state == "12.3"


async def test_an_answered_electrical_power_register_becomes_an_entity(hass, pump):
    """33126 is in no Weishaupt list; on a WBB 12 it tracked an external
    meter (r = 0.97, 2026-09) as the pump's own draw in watts."""
    pump.load_raw({"input": {ELECTRICAL_POWER: 656}})
    await _setup(hass, _entry(hass))

    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", CONST.DOMAIN, ELECTRICAL_POWER_UNIQUE_ID
    )
    assert entity_id, "the electrical power got no entity"
    state = hass.states.get(entity_id)
    assert float(state.state) == 656
    assert state.attributes["unit_of_measurement"] == "W"


async def test_firmware_without_the_electrical_power_register_gets_no_entity(
    hass, pump
):
    """Firmware that refuses 33126 (older ones may). An entity that can never show a
    value is clutter, unlike a module band that may come back."""
    pump.fail_read_band(ELECTRICAL_POWER)
    await _setup(hass, _entry(hass))

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("sensor", CONST.DOMAIN, ELECTRICAL_POWER_UNIQUE_ID)
        is None
    )
    outside = registry.async_get_entity_id(
        "sensor", CONST.DOMAIN, OUTSIDE_TEMPERATURE_UNIQUE_ID
    )
    assert hass.states.get(outside).state == "12.3"


async def test_settings_the_pump_only_reports_are_diagnostic(hass):
    """Read-only configuration registers and the undocumented ones crowded the
    device page between the temperatures and the setpoints people use."""
    await _setup(hass, _entry(hass))
    registry = er.async_get(hass)

    def category(unique_id):
        entity_id = registry.async_get_entity_id("sensor", CONST.DOMAIN, unique_id)
        assert entity_id, unique_id
        return registry.async_get(entity_id).entity_category

    for unique_id in (
        "weishaupt_wbbKonfiguration",
        "weishaupt_wbbW2_Konfiguration",
        "weishaupt_wbbAdr. 31106",
    ):
        assert category(unique_id) is EntityCategory.DIAGNOSTIC, unique_id
    assert category(OUTSIDE_TEMPERATURE_UNIQUE_ID) is None
    setpoint = registry.async_get_entity_id(
        "number", CONST.DOMAIN, COMFORT_ROOM_TEMPERATURE_UNIQUE_ID
    )
    assert registry.async_get(setpoint).entity_category is None


async def test_only_the_rows_without_a_meaning_of_their_own_start_disabled(hass):
    """31106 repeats the circuit's operating mode (41103) on every controller
    seen; enabled, it put the select's raw number on the device page as a
    sensor named by its address. 36801 is in no data-point list and read 0 on
    every controller seen. Held by the outside temperature alone, every
    diagnostic row could start disabled unnoticed."""
    rows = [
        row
        for device in DEVICELISTS
        for row in device
        if row.params.get("enabled_by_default") is False
    ]
    assert {row.address for row in rows} == {31106 + 100 * n for n in range(5)} | {
        36801
    }
    await _setup(hass, _entry(hass, data=ALL_CIRCUITS))

    disabled = {
        entry.unique_id: entry.disabled_by
        for entry in er.async_get(hass).entities.values()
        if entry.disabled_by is not None
    }
    assert disabled == {
        CONST.DEF_PREFIX + row.name: er.RegistryEntryDisabler.INTEGRATION
        for row in rows
    }


async def test_a_copy_of_the_operating_mode_that_exists_stays_enabled(hass):
    """Home Assistant applies the default only to an entity it creates: an
    update that disabled a sensor someone uses would break their setup."""
    entry = _entry(hass)
    unique_id = CONST.DEF_PREFIX + "Adr. 31106"
    er.async_get(hass).async_get_or_create(
        "sensor", CONST.DOMAIN, unique_id, config_entry=entry
    )

    await _setup(hass, entry)

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", CONST.DOMAIN, unique_id)
    assert registry.async_get(entity_id).disabled_by is None
    assert hass.states.get(entity_id) is not None


# Heating circuit configuration (41101, +100 per circuit): 0 = off.
CIRCUIT_CONFIGURATION = {2: 41201, 3: 41301, 4: 41401, 5: 41501}
CIRCUIT_OFF = 0
MIXING_CIRCUIT = 2
SETPOINT_CIRCUIT = 3
NOT_ENABLED = "circuit_not_enabled"
OFF_AT_CONTROLLER = "circuit_off_at_controller"


def _circuit_notice(hass, entry, kind, circuit):
    return ir.async_get(hass).async_get_issue(
        CONST.DOMAIN, f"{kind}_{entry.entry_id}_{circuit}"
    )


def _notices(hass, entry, kind):
    """The circuits a notice of this kind is raised for."""
    return [
        circuit
        for circuit in CIRCUIT_CONFIGURATION
        if _circuit_notice(hass, entry, kind, circuit) is not None
    ]


async def test_a_circuit_the_controller_sets_up_but_the_entry_leaves_off_is_named(
    hass, pump
):
    """A circuit the installer set up stayed out of Home Assistant with
    nothing to say it exists. Live, the controller reports a real circuit
    as 1 to 3 and one that is not there as 0, even where its band answers."""
    pump.load_raw(
        {
            "holding": {
                CIRCUIT_CONFIGURATION[2]: MIXING_CIRCUIT,
                CIRCUIT_CONFIGURATION[3]: 1,
                CIRCUIT_CONFIGURATION[5]: SETPOINT_CIRCUIT,
            }
        }
    )
    entry = await _setup(hass, _entry(hass))

    assert _notices(hass, entry, NOT_ENABLED) == [2, 3, 5]
    notice = _circuit_notice(hass, entry, NOT_ENABLED, 3)
    assert notice.translation_key == NOT_ENABLED
    assert notice.translation_placeholders == {"circuit": "3"}
    assert _notices(hass, entry, OFF_AT_CONTROLLER) == []


async def test_a_circuit_the_entry_polls_but_the_controller_has_off_is_named(
    hass, pump
):
    """On a one-circuit pump, circuits 2-4 answer factory values and look
    like circuits of their own once enabled."""
    pump.load_raw({"holding": {CIRCUIT_CONFIGURATION[3]: CIRCUIT_OFF}})
    entry = await _setup(hass, _entry(hass, data={**BASE_DATA, CONF.HK3: True}))

    assert _notices(hass, entry, OFF_AT_CONTROLLER) == [3]
    notice = _circuit_notice(hass, entry, OFF_AT_CONTROLLER, 3)
    assert notice.translation_key == OFF_AT_CONTROLLER
    assert notice.translation_placeholders == {"circuit": "3"}
    assert _notices(hass, entry, NOT_ENABLED) == []


async def test_matching_circuits_clear_an_earlier_notice(hass, pump):
    pump.load_raw({"holding": {CIRCUIT_CONFIGURATION[2]: MIXING_CIRCUIT}})
    entry = await _setup(hass, _entry(hass))
    assert _notices(hass, entry, NOT_ENABLED) == [2]

    await _reconfigure(hass, entry, {CONF.HK2: True})

    assert entry.state is ConfigEntryState.LOADED
    assert _notices(hass, entry, NOT_ENABLED) == []


async def test_an_ignored_circuit_notice_stays_ignored_but_not_for_another_circuit(
    hass, pump
):
    """Ignoring says "I do not want this circuit". One notice for every
    circuit kept a circuit set up later hidden behind the ignored one."""
    pump.load_raw({"holding": {CIRCUIT_CONFIGURATION[2]: MIXING_CIRCUIT}})
    entry = await _setup(hass, _entry(hass))
    ir.async_ignore_issue(hass, CONST.DOMAIN, f"{NOT_ENABLED}_{entry.entry_id}_2", True)

    pump.load_raw({"holding": {CIRCUIT_CONFIGURATION[3]: MIXING_CIRCUIT}})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert _circuit_notice(hass, entry, NOT_ENABLED, 2).dismissed_version is not None
    assert _circuit_notice(hass, entry, NOT_ENABLED, 3).dismissed_version is None


async def test_a_circuit_the_controller_refuses_is_not_judged(hass, pump):
    """The WBB refuses circuit 5's bands outright: no reading, no claim."""
    pump.fail_read_band(CIRCUIT_CONFIGURATION[5], register_type="holding")
    entry = await _setup(hass, _entry(hass, data={**BASE_DATA, CONF.HK5: True}))

    assert _notices(hass, entry, OFF_AT_CONTROLLER) == []


def _fail_circuit_read(monkeypatch, address, error):
    """The setup's read of one circuit fails; the polls still read its band."""
    read_word = WeishauptHeatPump._read_word

    async def failing(self, at):
        if at == address:
            raise error
        return await read_word(self, at)

    monkeypatch.setattr(WeishauptHeatPump, "_read_word", failing)


# Every way the controller can leave a circuit's setup untold: an exception
# code, a link that fails under the read, a word 41x01 does not document.
UNTOLD = {
    "refused": IllegalDataAddressError(),
    "busy": ServerDeviceBusyError(),
    "device-failure": ServerDeviceFailureError(),
    "gateway": GatewayTargetError(),
    "link-down": ModbusConnectionError("link down"),
    "timed-out": ModbusTimeoutError("timed out"),
    "undocumented-word": 4,
    "no-sensor-word": 0x8000,
}


@pytest.mark.parametrize(
    ("kind", "enabled", "told"),
    [
        pytest.param(NOT_ENABLED, False, MIXING_CIRCUIT, id="not-enabled"),
        pytest.param(OFF_AT_CONTROLLER, True, CIRCUIT_OFF, id="off-at-controller"),
    ],
)
@pytest.mark.parametrize("untold", UNTOLD.values(), ids=UNTOLD)
async def test_a_circuit_left_untold_keeps_its_notice_as_it_was(
    hass, pump, monkeypatch, kind, enabled, told, untold
):
    """A refused read deleted the notice and the user's ignore with it; the
    next good read raised it again, un-ignored."""
    address = CIRCUIT_CONFIGURATION[3]
    pump.load_raw({"holding": {address: told}})
    entry = await _setup(hass, _entry(hass, data={**BASE_DATA, CONF.HK3: enabled}))
    ir.async_ignore_issue(hass, CONST.DOMAIN, f"{kind}_{entry.entry_id}_3", True)
    ignored = _circuit_notice(hass, entry, kind, 3)

    if isinstance(untold, Exception):
        _fail_circuit_read(monkeypatch, address, untold)
    else:
        pump.load_raw({"holding": {address: untold}})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert _circuit_notice(hass, entry, kind, 3) == ignored


@pytest.mark.parametrize(
    ("kind", "enabled", "told"),
    [
        pytest.param(NOT_ENABLED, False, MIXING_CIRCUIT, id="not-enabled"),
        pytest.param(OFF_AT_CONTROLLER, True, CIRCUIT_OFF, id="off-at-controller"),
    ],
)
@pytest.mark.parametrize("untold", UNTOLD.values(), ids=UNTOLD)
async def test_a_notice_the_entry_now_contradicts_goes_though_the_circuit_is_untold(
    hass, pump, monkeypatch, kind, enabled, told, untold
):
    """Kept for an untold circuit, "circuit 3 is not read" outlived the
    reconfigure that enabled it. The entry's half of a notice needs no
    answer from the controller."""
    address = CIRCUIT_CONFIGURATION[3]
    pump.load_raw({"holding": {address: told}})
    entry = await _setup(hass, _entry(hass, data={**BASE_DATA, CONF.HK3: enabled}))
    ir.async_ignore_issue(hass, CONST.DOMAIN, f"{kind}_{entry.entry_id}_3", True)

    if isinstance(untold, Exception):
        _fail_circuit_read(monkeypatch, address, untold)
    else:
        pump.load_raw({"holding": {address: untold}})
    await _reconfigure(hass, entry, {CONF.HK3: not enabled})

    assert entry.state is ConfigEntryState.LOADED
    assert _circuit_notice(hass, entry, NOT_ENABLED, 3) is None
    assert _circuit_notice(hass, entry, OFF_AT_CONTROLLER, 3) is None


@pytest.mark.parametrize(
    "error",
    [
        ModbusConnectionError("link down"),
        ModbusTimeoutError("no answer"),
        TimeoutError(),
    ],
    ids=["link-down", "timed-out", "time-limit"],
)
async def test_a_link_lost_during_the_circuit_check_still_loads_the_entry(
    hass, pump, monkeypatch, caplog, error
):
    """The check is only a hint; the polls report a link that drops."""

    async def drops(_self):
        raise error

    monkeypatch.setattr(WeishauptHeatPump, "circuit_configurations", drops)
    entry = await _setup(hass, _entry(hass, data={**BASE_DATA, CONF.HK3: True}))

    assert entry.state is ConfigEntryState.LOADED
    assert _notices(hass, entry, OFF_AT_CONTROLLER) == []
    assert not _integration_warnings(caplog), "a dropped link is not a fault"


async def test_a_cancel_during_the_circuit_check_is_not_swallowed(
    hass, pump, monkeypatch
):
    """A handler wide enough to catch it loaded the entry of a setup that
    Home Assistant had called off."""

    async def cancelled(_self):
        raise asyncio.CancelledError

    monkeypatch.setattr(WeishauptHeatPump, "circuit_configurations", cancelled)
    entry = _entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is not ConfigEntryState.LOADED


@pytest.mark.parametrize(
    "error", [RuntimeError("bug"), KeyError("bug")], ids=["runtime", "key"]
)
async def test_an_unexpected_error_in_the_circuit_check_still_loads_the_entry(
    hass, pump, monkeypatch, caplog, error
):
    """A fault of this code or a library failed the whole setup, without a
    retry, for what is only a hint."""

    async def fails(_self):
        raise error

    monkeypatch.setattr(WeishauptHeatPump, "circuit_configurations", fails)
    entry = await _setup(hass, _entry(hass))

    assert entry.state is ConfigEntryState.LOADED
    tracebacks = [
        record
        for record in caplog.records
        if record.name.startswith("custom_components.weishaupt_modbus")
        and record.exc_info
        and record.exc_info[1] is error
    ]
    assert len(tracebacks) == 1


async def test_removing_the_entry_takes_its_circuit_notices_along(hass, pump):
    pump.load_raw(
        {
            "holding": {
                CIRCUIT_CONFIGURATION[2]: MIXING_CIRCUIT,
                CIRCUIT_CONFIGURATION[3]: CIRCUIT_OFF,
                CIRCUIT_CONFIGURATION[5]: MIXING_CIRCUIT,
            }
        }
    )
    entry = await _setup(hass, _entry(hass, data={**BASE_DATA, CONF.HK3: True}))
    assert _notices(hass, entry, NOT_ENABLED) == [2, 5]
    assert _notices(hass, entry, OFF_AT_CONTROLLER) == [3]

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert _notices(hass, entry, NOT_ENABLED) == []
    assert _notices(hass, entry, OFF_AT_CONTROLLER) == []


async def test_icons_come_from_the_icon_translations(hass):
    """An icon set in code bypasses icons.json: it cannot follow a state and
    is not where Home Assistant looks for one."""
    await _setup(hass, _entry(hass))
    registry = er.async_get(hass)
    error = registry.async_get_entity_id("sensor", CONST.DOMAIN, "weishaupt_wbbFehler")
    curve = registry.async_get_entity_id(
        "number", CONST.DOMAIN, "weishaupt_wbbHeizkennlinie"
    )

    for entity_id in (error, curve):
        assert "icon" not in hass.states.get(entity_id).attributes, entity_id
    icons = json.loads((INTEGRATION / "icons.json").read_text(encoding="utf-8"))
    assert icons["entity"]["sensor"]["fehler"]["default"] == "mdi:alert"
    assert icons["entity"]["number"]["heizkennlinie"]["default"] == "mdi:chart-line"


async def test_diagnostics_show_what_the_pump_answered_without_its_address(
    hass, hass_client
):
    """A problem report needs the register values and which bands the pump
    refused; it must not carry the pump's address."""
    entry = await _setup(hass, _entry(hass))

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["entry"]["data"][CONF.HOST] == "**REDACTED**"
    assert "192.0.2.10" not in str(diagnostics)
    assert diagnostics["entry"]["data"][CONF.KENNFELD_FILE] == "**REDACTED**"
    assert diagnostics["coordinator"]["last_update_success"] is True
    assert diagnostics["bands"]["30001-30006"] is True
    outside = next(
        row for row in diagnostics["registers"] if row["address"] == OUTSIDE_TEMPERATURE
    )
    assert outside["state"] == 123
    assert diagnostics["write_counters"] == {"total": 0, "today": 0}


async def test_diagnostics_carry_none_of_the_names_a_user_typed(hass, hass_client):
    """The download is meant for a public issue. Prefix, postfix and the map's
    file name are free text - a family name, "keller" - and went out as typed."""
    marker = "PRIVATE_LOCATION_MARKER"
    entry = await _setup(
        hass,
        _entry(
            hass,
            data={
                **BASE_DATA,
                CONF.PREFIX: marker,
                CONF.DEVICE_POSTFIX: marker,
                CONF.KENNFELD_FILE: f"{marker}_kennfeld.json",
            },
        ),
    )

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert marker not in str(diagnostics)
    assert BASE_DATA[CONF.HOST] not in str(diagnostics)


async def test_setup_creates_all_three_platforms(hass):
    await _setup(hass, _entry(hass))

    registry = er.async_get(hass)
    domains = {entry.domain for entry in registry.entities.values()}
    assert {"sensor", "select", "number"} <= domains


async def test_unload_releases_the_shared_connection(hass, mock_modbus):
    """The last entry to let go of the shared connection has to close it;
    left open, it holds a socket at the controller that nothing uses."""
    entry = await _setup(hass, _entry(hass))
    assert mock_modbus.connected

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not mock_modbus.connected, "the Modbus connection was left open"


async def test_an_old_entry_migrates_to_the_current_version(hass):
    """A version-1 entry carries only the host; every later key has to be
    filled in, or the entity setup reads a key that is not there."""
    entry = _entry(hass, data={CONF.HOST: "192.0.2.10"}, version=1)

    await _setup(hass, entry)

    assert entry.version == 11
    for key in (
        CONF.PREFIX,
        CONF.DEVICE_POSTFIX,
        CONF.KENNFELD_FILE,
        CONF.HK2,
        CONF.HK5,
        CONF.NAME_DEVICE_PREFIX,
        CONF.NAME_TOPIC_PREFIX,
    ):
        assert key in entry.data, f"migration left {key!r} out"
    assert entry.state is ConfigEntryState.LOADED
    # A version-1 entry has no port; the select platform read it unguarded
    # and failed to set up while the entry itself reported LOADED.
    assert entry.data[CONF.PORT] == DEFAULT_PORT
    assert entry.unique_id == "192.0.2.10:502"
    registry = er.async_get(hass)
    platforms = {
        registry_entry.domain
        for registry_entry in er.async_entries_for_config_entry(
            registry, entry.entry_id
        )
    }
    assert {"sensor", "select", "number"} <= platforms, (
        f"a platform failed to set up after the migration: {platforms}"
    )


async def test_two_legacy_entries_on_one_pump_do_not_get_the_same_identity(
    hass, caplog
):
    """Before version 11 nothing stopped two entries on one host. The first
    to migrate keeps the identity; the second is left without one and the
    log says which two to look at."""
    first = _entry(hass, version=10)
    second = _entry(hass, data={**BASE_DATA, CONF.DEVICE_POSTFIX: "2"}, version=10)

    # Loading the domain loads every entry of it, in the order they were added.
    await _setup(hass, first)
    await hass.async_block_till_done()

    assert first.unique_id == "192.0.2.10:502"
    assert second.unique_id is None
    assert "same host and port" in caplog.text


async def test_a_web_interface_entry_is_stripped_of_its_settings_and_entities(hass):
    """Versions 5 to 8 stored web-interface credentials and switches in the
    entry and registered web-interface sensors. Version 9 takes both out, so
    a password does not stay on disk and no orphaned entity lingers."""
    legacy = {
        **BASE_DATA,
        "enable-webif": True,
        "username": "user",
        "password": "secret",
        "Web-IF-Token": "token",
        "Poll Heizkreis 1": True,
    }
    entry = _entry(hass, data=legacy, version=8)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor",
        CONST.DOMAIN,
        "webif_info_waermepumpe_betrieb",
        config_entry=entry,
    )

    await _setup(hass, entry)

    assert entry.version == 11
    for key in (
        "enable-webif",
        "username",
        "password",
        "Web-IF-Token",
        "Poll Heizkreis 1",
    ):
        assert key not in entry.data, f"{key!r} survived the migration"
    assert entry.data[CONF.HOST] == BASE_DATA[CONF.HOST]
    assert (
        registry.async_get_entity_id(
            "sensor", CONST.DOMAIN, "webif_info_waermepumpe_betrieb"
        )
        is None
    ), "the web-interface entity was left in the registry"


@pytest.mark.parametrize(
    ("postfix", "web_interface_id", "pump_id"),
    [
        ("", "weishaupt_wbb_Außentemperatur_webif", OUTSIDE_TEMPERATURE_UNIQUE_ID),
        (
            "webif",
            "weishaupt_wbb_Außentemperatur_webif_webif",
            OUTSIDE_TEMPERATURE_UNIQUE_ID + "_webif",
        ),
    ],
    ids=["no postfix", "postfix webif"],
)
async def test_the_web_interface_entities_of_1_0_19_go_too(
    hass, postfix, web_interface_id, pump_id
):
    """1.0.18 and 1.0.19 named them prefix, "_", name, postfix and "_webif";
    version 9 looked for the "webif_" of a later pre-release only, and they
    stayed unavailable for good. A pump whose postfix is "webif" keeps its
    own entities, whose ids end the same way."""
    entry = _entry(hass, data={**BASE_DATA, CONF.DEVICE_POSTFIX: postfix}, version=8)
    registry = er.async_get(hass)
    web_interface = registry.async_get_or_create(
        "sensor", CONST.DOMAIN, web_interface_id, config_entry=entry
    ).entity_id
    pump_entity = registry.async_get_or_create(
        "sensor", CONST.DOMAIN, pump_id, config_entry=entry
    ).entity_id
    # Removed and added again by the setup, an entity is back in the registry
    # at once; the removal is what shows.
    removed = []

    def note_removal(event):
        if event.data["action"] == "remove":
            removed.append(event.data["entity_id"])

    hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, note_removal)

    await _setup(hass, entry)

    assert removed == [web_interface]
    assert registry.async_get_entity_id("sensor", CONST.DOMAIN, pump_id) == pump_entity


# The three 2nd-heat-source entities as version 9 registered them on an
# English Home Assistant: unique id from the old item name, entity id from the
# device and the old label.
RELABELLED_BEFORE_V10 = {
    "weishaupt_wbbSchaltspiele E-Heizung 1": "sensor.wh_2nd_heat_source_switching_cycles_e_heating_1",
    "weishaupt_wbbBetriebsstunden E1": "sensor.wh_2nd_heat_source_operation_hours_e1",
    "weishaupt_wbbSchaltspiele E-Heizung 2": "sensor.wh_2nd_heat_source_switching_cycles_e_heating_2",
}
RELABELLED_AFTER_V10 = {
    "weishaupt_wbbBetriebsstunden 2. WEZ": "sensor.wh_2nd_heat_source_operation_hours_2nd_heat_source",
    "weishaupt_wbbSchaltspiele 2. WEZ": "sensor.wh_2nd_heat_source_switching_cycles_2nd_heat_source",
    "weishaupt_wbbBetriebsstunden E1": "sensor.wh_2nd_heat_source_operation_hours_e1",
}


def _register_v9_second_heat_source(hass, entry):
    registry = er.async_get(hass)
    for unique_id, entity_id in RELABELLED_BEFORE_V10.items():
        registry.async_get_or_create(
            "sensor",
            CONST.DOMAIN,
            unique_id,
            config_entry=entry,
            suggested_object_id=entity_id.split(".", 1)[1],
        )
    return registry


async def test_relabelled_registers_take_their_history_and_auto_ids_along(hass):
    """Version 10 corrects three 2nd-heat-source labels. The unique id carries
    the item name, so without a migration every one of them would come back
    as a new entity with an empty history - and the old ones would linger as
    unavailable. An entity id that is still the auto-generated one follows
    the label; the old "operation hours E1" id ends up on the register that
    really counts those hours."""
    entry = _entry(hass, version=9)
    registry = _register_v9_second_heat_source(hass, entry)

    await _setup(hass, entry)

    for unique_id, entity_id in RELABELLED_AFTER_V10.items():
        assert (
            registry.async_get_entity_id("sensor", CONST.DOMAIN, unique_id) == entity_id
        )
    for unique_id in RELABELLED_BEFORE_V10:
        if unique_id not in RELABELLED_AFTER_V10:
            assert (
                registry.async_get_entity_id("sensor", CONST.DOMAIN, unique_id) is None
            )


async def test_a_hand_made_id_that_merely_contains_the_old_words_is_kept(hass):
    """Only the generated form - the slug at the END of the object id - is
    renamed; the same words inside a user's own id are the user's."""
    entry = _entry(hass, version=9)
    registry = _register_v9_second_heat_source(hass, entry)
    registry.async_update_entity(
        "sensor.wh_2nd_heat_source_switching_cycles_e_heating_2",
        new_entity_id="sensor.my_switching_cycles_e_heating_2_notes",
    )

    await _setup(hass, entry)

    assert (
        registry.async_get_entity_id(
            "sensor", CONST.DOMAIN, "weishaupt_wbbBetriebsstunden E1"
        )
        == "sensor.my_switching_cycles_e_heating_2_notes"
    )


async def test_a_hand_renamed_entity_keeps_its_id_through_the_relabelling(hass, caplog):
    entry = _entry(hass, version=9)
    registry = _register_v9_second_heat_source(hass, entry)
    registry.async_update_entity(
        "sensor.wh_2nd_heat_source_switching_cycles_e_heating_2",
        new_entity_id="sensor.backup_heater_hours",
    )

    await _setup(hass, entry)

    assert (
        registry.async_get_entity_id(
            "sensor", CONST.DOMAIN, "weishaupt_wbbBetriebsstunden E1"
        )
        == "sensor.backup_heater_hours"
    )
    assert "sensor.backup_heater_hours (kept" in caplog.text


async def test_a_renamed_entity_keeps_its_id_across_a_restart(hass):
    """Issue #146: every setup re-ran an entity-id "migration" that forced the
    German default id back onto every entity - a user who renamed
    `sensor.wh_system_aussentemperatur` to something else got the rename
    undone on the next restart, and the automations built on it broke."""
    entry = await _setup(hass, _entry(hass))
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", CONST.DOMAIN, OUTSIDE_TEMPERATURE_UNIQUE_ID
    )
    registry.async_update_entity(
        entity_id, new_entity_id="sensor.my_outside_temperature"
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    await _setup(hass, entry)

    assert (
        registry.async_get_entity_id(
            "sensor", CONST.DOMAIN, OUTSIDE_TEMPERATURE_UNIQUE_ID
        )
        == "sensor.my_outside_temperature"
    ), "the restart renamed the entity back"


async def test_reconfigure_reloads_the_entry_once(hass, mock_modbus, caplog):
    """Issue #180: Home Assistant warns - and from 2026.12 refuses - when a
    flow schedules a reload itself while the entry also has an update
    listener, because that reloads twice. The flow is the one path here."""
    entry = await _setup(hass, _entry(hass))

    result = await hass.config_entries.flow.async_init(
        CONST.DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    reconfigure_page = {
        key: value
        for key, value in BASE_DATA.items()
        if key not in (CONF.PREFIX, CONF.DEVICE_POSTFIX)
    }
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**reconfigure_page, CONF.HOST: "192.0.2.20"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF.HOST] == "192.0.2.20"
    # The flow probes the new host once, then reloads the entry once. Any
    # third connection to it is a second reload.
    on_new_host = [p for p in mock_modbus.params_seen if p.host == "192.0.2.20"]
    assert len(on_new_host) == 2, (
        f"the new host was connected {len(on_new_host)} times (probe + reloads); "
        "the entry should reload exactly once"
    )
    assert "has an update listener and should use it" not in caplog.text, (
        "Home Assistant reported the double-reload deprecation"
    )


@pytest.mark.parametrize(
    "change",
    [{"title": "Garage"}, {"pref_disable_new_entities": True}],
    ids=["renamed", "new entities off"],
)
async def test_a_change_the_setup_did_not_read_does_not_reload(
    hass, mock_modbus, change
):
    """Renaming the entry reloaded it: every entity went unavailable for a
    moment, and an automation watching for that fired."""
    entry = await _setup(hass, _entry(hass))
    loaded = entry.runtime_data

    hass.config_entries.async_update_entry(entry, **change)
    await hass.async_block_till_done()

    assert entry.runtime_data is loaded


async def test_the_options_flow_sets_the_poll_interval(hass, mock_modbus):
    """Issue #183: the poll interval is a runtime setting - changed in the
    options dialog, stored in entry.options, picked up by the coordinator
    after the reload the options trigger."""
    entry = await _setup(hass, _entry(hass))
    assert entry.runtime_data.coordinator.update_interval == CONST.SCAN_INTERVAL

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONST.OPTION_SCAN_INTERVAL: 60}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONST.OPTION_SCAN_INTERVAL] == 60
    assert len(mock_modbus.params_seen) == 2, "the change was not applied by a reload"
    assert entry.runtime_data.coordinator.update_interval.total_seconds() == 60


async def test_a_warning_above_the_limit_is_refused(hass):
    """Writes are refused at the limit, so a warning threshold above it
    could never fire; the form said nothing and stored it."""
    entry = await _setup(hass, _entry(hass))

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONST.OPTION_SCAN_INTERVAL: 60,
            CONST.OPTION_WRITE_WARNING_PER_DAY: 60,
            CONST.OPTION_WRITE_LIMIT_PER_DAY: 50,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "warning_above_limit"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONST.OPTION_SCAN_INTERVAL: 60,
            CONST.OPTION_WRITE_WARNING_PER_DAY: 50,
            CONST.OPTION_WRITE_LIMIT_PER_DAY: 60,
        },
    )
    # The options reload the entry; left running, that reload ended inside
    # Home Assistant's stop at teardown, now and then (a lingering timer).
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_an_out_of_range_poll_interval_is_refused(hass):
    entry = await _setup(hass, _entry(hass))

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONST.OPTION_SCAN_INTERVAL: 1}
        )


PV_SETPOINT = 40002
WRITES_TOTAL_UNIQUE_ID = "weishaupt_wbbeeprom_writes_total"
WRITES_TODAY_UNIQUE_ID = "weishaupt_wbbeeprom_writes_today"


async def test_the_write_counters_survive_a_restart(hass):
    """Issue #187: a counter that starts at zero on every restart tells the
    user nothing about the 100 000 writes the EEPROM is rated for."""
    entry = await _setup(hass, _entry(hass))
    registry = er.async_get(hass)
    total_id = registry.async_get_entity_id(
        "sensor", CONST.DOMAIN, WRITES_TOTAL_UNIQUE_ID
    )
    today_id = registry.async_get_entity_id(
        "sensor", CONST.DOMAIN, WRITES_TODAY_UNIQUE_ID
    )
    assert hass.states.get(total_id).state == "0"

    pump = entry.runtime_data.coordinator.device
    setpoint = next(row for row in pump.items if row.address == PV_SETPOINT)
    await pump.write(setpoint, 5)
    await hass.async_block_till_done()
    # At once, not after the next poll: a reload in between restored 0.
    assert hass.states.get(total_id).state == "1"
    assert hass.states.get(today_id).state == "1"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    await _setup(hass, entry)

    assert hass.states.get(total_id).state == "1", "the total was lost on reload"
    assert hass.states.get(today_id).state == "1", "today's count was lost on reload"
    assert entry.runtime_data.coordinator.device.write_budget.total == 1, (
        "the sensor shows the old number but the client counts from zero again"
    )


async def test_todays_write_count_survives_a_reload_while_polling_fails(hass):
    """The counter went unavailable with the polling, and its day with it: a
    reload restored today's count as 0, and the daily limit was free again."""
    entry = await _setup(hass, _entry(hass))
    device = entry.runtime_data.coordinator.device
    setpoint = next(row for row in device.items if row.address == PV_SETPOINT)
    await device.write(setpoint, 5)
    await hass.async_block_till_done()
    entry.runtime_data.coordinator.async_set_update_error(UpdateFailed("link down"))
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.coordinator.device.write_budget.writes_today == 1


async def _write_once(hass, entry):
    device = entry.runtime_data.coordinator.device
    setpoint = next(row for row in device.items if row.address == PV_SETPOINT)
    await device.write(setpoint, setpoint.state + 1 if setpoint.state else 5)
    await hass.async_block_till_done()


def _counts(entry):
    budget = entry.runtime_data.coordinator.device.write_budget
    return budget.total, budget.writes_today


async def test_the_write_counts_survive_a_reload_with_their_sensors_disabled(hass):
    """The counts lived in the sensors' last states: disabled, the sensors
    restored nothing, and every reload freed the daily limit again."""
    entry = _entry(hass)
    registry = er.async_get(hass)
    for unique_id in (WRITES_TOTAL_UNIQUE_ID, WRITES_TODAY_UNIQUE_ID):
        registry.async_get_or_create(
            "sensor",
            CONST.DOMAIN,
            unique_id,
            config_entry=entry,
            disabled_by=er.RegistryEntryDisabler.USER,
        )
    await _setup(hass, entry)
    await _write_once(hass, entry)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert _counts(entry) == (1, 1)


async def test_a_counter_renamed_and_renamed_back_counts_its_writes_once(hass):
    """Added again under its old id, the sensor added its recorded count to
    the budget a second time, and the daily limit stopped writes too early."""
    entry = await _setup(hass, _entry(hass))
    await _write_once(hass, entry)
    registry = er.async_get(hass)
    total_id = registry.async_get_entity_id(
        "sensor", CONST.DOMAIN, WRITES_TOTAL_UNIQUE_ID
    )

    for new_id in ("sensor.writes_elsewhere", total_id):
        current = registry.async_get_entity_id(
            "sensor", CONST.DOMAIN, WRITES_TOTAL_UNIQUE_ID
        )
        registry.async_update_entity(current, new_entity_id=new_id)
        await hass.async_block_till_done()

    assert _counts(entry) == (1, 1)


async def test_the_counts_the_sensors_recorded_are_taken_over_once(hass):
    """Before the counts had a store of their own, the sensors held them: the
    first start takes them over, every later one reads the store."""
    entry = _entry(hass)
    registry = er.async_get(hass)
    ids = [
        registry.async_get_or_create(
            "sensor", CONST.DOMAIN, unique_id, config_entry=entry
        ).entity_id
        for unique_id in (WRITES_TOTAL_UNIQUE_ID, WRITES_TODAY_UNIQUE_ID)
    ]
    today = {"day": dt_util.now().date().isoformat()}
    recorded = {"native_value": 7, "native_unit_of_measurement": None}
    mock_restore_cache_with_extra_data(
        hass,
        [
            (State(ids[0], "7"), recorded),
            (State(ids[1], "2", today), {**recorded, "native_value": 2}),
        ],
    )
    await _setup(hass, entry)
    assert _counts(entry) == (7, 2)

    # The sensors record no state any more: the next start has only the store.
    async_get_restored(hass).last_states.clear()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _counts(entry) == (7, 2), "the counts taken over were not saved"

    await _write_once(hass, entry)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert _counts(entry) == (8, 3)


async def test_a_count_recorded_as_unavailable_is_taken_over_as_zero(hass):
    """Before the store, a counter went unavailable with the polling, and its
    last state then held no count."""
    entry = _entry(hass)
    registry = er.async_get(hass)
    ids = [
        registry.async_get_or_create(
            "sensor", CONST.DOMAIN, unique_id, config_entry=entry
        ).entity_id
        for unique_id in (WRITES_TOTAL_UNIQUE_ID, WRITES_TODAY_UNIQUE_ID)
    ]
    mock_restore_cache_with_extra_data(
        hass,
        [
            (State(ids[0], "5"), {"native_value": 5}),
            (State(ids[1], "unavailable"), {"native_value": None}),
        ],
    )

    await _setup(hass, entry)

    assert _counts(entry) == (5, 0)


async def test_removing_the_entry_removes_its_write_counts(hass, hass_storage):
    entry = await _setup(hass, _entry(hass))
    await _write_once(hass, entry)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=1))
    await hass.async_block_till_done()
    stored = [key for key in hass_storage if entry.entry_id in key]
    assert stored, "the counts were never saved"

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert not [key for key in hass_storage if entry.entry_id in key]


PV_SETPOINT_UNIQUE_ID = "weishaupt_wbbSollwertPV"


async def test_a_refused_write_reaches_the_user_as_an_error(hass, monkeypatch):
    """A write the pump (or the daily limit) refuses used to be logged and
    swallowed: the slider snapped back with no word why, and an automation
    calling the service believed it had succeeded."""
    entry = await _setup(hass, _entry(hass))
    entity_id = er.async_get(hass).async_get_entity_id(
        "number", CONST.DOMAIN, PV_SETPOINT_UNIQUE_ID
    )

    async def refuse(self, item, value, check=None):
        raise ServerDeviceFailureError

    monkeypatch.setattr(WeishauptHeatPump, "write", refuse)

    with pytest.raises(HomeAssistantError) as raised:
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": 5},
            blocking=True,
        )
    assert raised.value.translation_key == "write_failed"
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(
    ("domain", "service", "data"),
    [
        ("number", "set_value", {"value": 5}),
        ("switch", "turn_off", {}),
        ("switch", "turn_on", {}),
    ],
)
async def test_the_daily_write_limit_reaches_the_user_in_their_language(
    hass, pump, monkeypatch, domain, service, data
):
    """The limit's own English sentence went into the translated "write
    failed" message, on the number and on both switch paths."""
    pump.load_raw({"holding": {SG_READY_BOOST: 50}})
    await _setup(hass, _entry(hass))
    unique_id = SG_READY_BOOST_UNIQUE_ID + ("_active" if domain == "switch" else "")
    entity_id = er.async_get(hass).async_get_entity_id(domain, CONST.DOMAIN, unique_id)

    async def over_the_limit(self, item, *_args, **_kwargs):
        raise WriteError(1, item.address)

    monkeypatch.setattr(WeishauptHeatPump, "write", over_the_limit)
    monkeypatch.setattr(WeishauptHeatPump, "write_off", over_the_limit)

    with pytest.raises(HomeAssistantError) as raised:
        await hass.services.async_call(
            domain, service, {"entity_id": entity_id, **data}, blocking=True
        )
    assert raised.value.translation_key == "write_limit_reached"
    assert raised.value.translation_placeholders == {"limit": "1"}


async def test_a_pump_that_refuses_the_first_connection_retries_later(hass, pump):
    pump.fail_requests(ModbusConnectionError("connection refused"))
    entry = _entry(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


HEATING_CIRCUIT_2_ROOM_TEMPERATURE = 31202


async def test_a_disabled_heating_circuit_is_neither_polled_nor_an_entity(hass, pump):
    """Circuits 2-5 were read on every poll and registered as entities
    showing unknown, whatever the entry said."""
    await _setup(hass, _entry(hass))

    registry = er.async_get(hass)
    assert not any(
        entry.unique_id.endswith("Raumtemperatur_2") or "heizkreis2" in entry.entity_id
        for entry in registry.entities.values()
    )
    assert not any(
        read.address
        <= HEATING_CIRCUIT_2_ROOM_TEMPERATURE
        <= read.address + read.count - 1
        for read in pump.unit.read_events
    ), "heating circuit 2 was polled although it is disabled"


@pytest.mark.parametrize(("postfix", "suffix"), [("", ""), ("2", "_2")])
async def test_a_circuit_switched_off_leaves_no_device_behind(
    hass, pump, postfix, suffix
):
    """Following the notice, the user switched circuit 2 off: its device
    stayed with every entity unavailable, and even emptied it could not be
    deleted. Switched on again, the device and the ids the user chose return."""
    changes = async_capture_events(hass, dr.EVENT_DEVICE_REGISTRY_UPDATED)
    data = {**BASE_DATA, CONF.HK2: True, CONF.DEVICE_POSTFIX: postfix}
    entry = await _setup(hass, _entry(hass, data=data))
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    devices, entities = dr.async_get(hass), er.async_get(hass)
    identifier = (CONST.DOMAIN, DeviceConstants.HZ2 + suffix)
    circuit = devices.async_get_device_by_identifier(identifier, entry.entry_id)
    sensor = next(
        row
        for row in er.async_entries_for_device(entities, circuit.id)
        if row.domain == "sensor"
    )
    entities.async_update_entity(sensor.entity_id, new_entity_id="sensor.my_circuit")

    await _reconfigure(hass, entry, {CONF.HK2: False})

    assert devices.async_get(circuit.id) is None
    assert not er.async_entries_for_device(
        entities, circuit.id, include_disabled_entities=True
    )

    await _reconfigure(hass, entry, {CONF.HK2: True})

    restored = devices.async_get_device_by_identifier(identifier, entry.entry_id)
    assert restored.id == circuit.id
    removed = [event.data for event in changes if event.data["action"] == "remove"]
    assert [change["device_id"] for change in removed] == [circuit.id], (
        "a circuit still enabled lost its device too"
    )
    assert entities.async_get("sensor.my_circuit"), "the id the user chose was lost"


async def test_an_enabled_heating_circuit_is_polled(hass, pump):
    await _setup(hass, _entry(hass, data={**BASE_DATA, CONF.HK2: True}))

    assert any(
        read.address
        <= HEATING_CIRCUIT_2_ROOM_TEMPERATURE
        <= read.address + read.count - 1
        for read in pump.unit.read_events
    )
