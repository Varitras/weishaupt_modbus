"""End-to-end tests against a real Home Assistant instance.

These exercise the parts that only exist once Home Assistant itself is driving
the integration: entry setup and unload, the config-entry migration, and the
entity platforms. Everything below the coordinator (the Modbus wire) is faked -
the point here is the Home Assistant contract, not the client.

Marked `e2e` because each test boots a full Home Assistant instance; the
everyday run deselects them, CI runs them with `-m ""`.
"""

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
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.weishaupt_modbus.configentry import host_lock
from custom_components.weishaupt_modbus.const import CONF, CONST
from custom_components.weishaupt_modbus.weishaupt_modbus_api.const import DEFAULT_PORT
from custom_components.weishaupt_modbus.weishaupt_modbus_api.device import (
    WeishauptHeatPump,
)
from custom_components.weishaupt_modbus.weishaupt_modbus_api.exceptions import (
    WriteError,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EntityCategory
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er, issue_registry as ir

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


FLOW_SETPOINT = 31104
FLOW_SETPOINT_UNIQUE_ID = "weishaupt_wbbVorlaufsolltemperatur"


@pytest.mark.parametrize("no_demand", [1, 0x8000])
async def test_a_flow_setpoint_with_no_demand_reads_zero_in_home_assistant(
    hass, pump, no_demand
):
    """Live, the controller reported 1 on 31104 all summer. The unit test
    holds the entity alone; this holds the state Home Assistant shows."""
    pump.load_raw({"input": {FLOW_SETPOINT: no_demand}})
    await _setup(hass, _entry(hass))
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", CONST.DOMAIN, FLOW_SETPOINT_UNIQUE_ID
    )

    state = hass.states.get(entity_id)
    assert float(state.state) == 0
    assert state.attributes["demand"] == "none"


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


async def test_the_copy_of_the_operating_mode_starts_disabled(hass):
    """31106 repeats the circuit's operating mode (41103) on every controller
    seen; enabled, it put an unnamed second copy of the select on the device
    page. Only new entities start disabled: an existing one keeps its state."""
    await _setup(hass, _entry(hass, data={**BASE_DATA, CONF.HK2: True}))
    registry = er.async_get(hass)

    def disabled_by(unique_id):
        entity_id = registry.async_get_entity_id("sensor", CONST.DOMAIN, unique_id)
        assert entity_id, unique_id
        return registry.async_get(entity_id).disabled_by

    for unique_id in ("weishaupt_wbbAdr. 31106", "weishaupt_wbbAdr. 311062"):
        assert disabled_by(unique_id) is er.RegistryEntryDisabler.INTEGRATION
    assert disabled_by(OUTSIDE_TEMPERATURE_UNIQUE_ID) is None


# Heating circuit configuration (41101, +100 per circuit): 0 = off.
CIRCUIT_CONFIGURATION = {2: 41201, 3: 41301, 4: 41401, 5: 41501}
CIRCUIT_OFF = 0
MIXING_CIRCUIT = 2
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
            }
        }
    )
    entry = await _setup(hass, _entry(hass))

    assert _notices(hass, entry, NOT_ENABLED) == [2, 3]
    notice = _circuit_notice(hass, entry, NOT_ENABLED, 3)
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
    assert notice.translation_placeholders == {"circuit": "3"}
    assert _notices(hass, entry, NOT_ENABLED) == []


async def test_matching_circuits_clear_an_earlier_notice(hass, pump):
    pump.load_raw({"holding": {CIRCUIT_CONFIGURATION[2]: MIXING_CIRCUIT}})
    entry = await _setup(hass, _entry(hass))
    assert _notices(hass, entry, NOT_ENABLED) == [2]

    # The data change reloads the entry through its update listener.
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF.HK2: True})
    await hass.async_block_till_done()

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


async def test_a_link_lost_during_the_circuit_check_still_loads_the_entry(
    hass, pump, monkeypatch, caplog
):
    """The check is only a hint; the polls report a link that drops."""

    async def drops(_self):
        raise ModbusConnectionError("link down")

    monkeypatch.setattr(WeishauptHeatPump, "circuit_configurations", drops)
    entry = await _setup(hass, _entry(hass, data={**BASE_DATA, CONF.HK3: True}))

    assert entry.state is ConfigEntryState.LOADED
    assert _notices(hass, entry, OFF_AT_CONTROLLER) == []
    assert not _integration_warnings(caplog), "a dropped link is not a fault"


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
                CIRCUIT_CONFIGURATION[4]: MIXING_CIRCUIT,
            }
        }
    )
    entry = await _setup(hass, _entry(hass))
    assert _notices(hass, entry, NOT_ENABLED) == [2, 4]

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert _notices(hass, entry, NOT_ENABLED) == []


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
    """The controller allows one TCP connection; the last entry to let go of
    the shared one has to close it, or the next load finds the port busy."""
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


async def test_reconfigure_reloads_once_through_the_update_listener(
    hass, mock_modbus, caplog
):
    """Issue #180: Home Assistant warns - and from 2026.12 refuses - when a
    flow schedules a reload itself while the entry also has an update
    listener, because that reloads twice. The listener is the one path here:
    the flow only updates the entry and aborts."""
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
    # The flow probes the new host once; then the update listener reloads
    # the entry once. Any third connection to it is a second reload.
    on_new_host = [p for p in mock_modbus.params_seen if p.host == "192.0.2.20"]
    assert len(on_new_host) == 2, (
        f"the new host was connected {len(on_new_host)} times (probe + reloads); "
        "the update listener should reload the entry exactly once"
    )
    assert "has an update listener and should use it" not in caplog.text, (
        "Home Assistant reported the double-reload deprecation"
    )


async def test_the_options_flow_sets_the_poll_interval(hass, mock_modbus):
    """Issue #183: the poll interval is a runtime setting - changed in the
    options dialog, stored in entry.options, picked up by the coordinator
    after the reload the update listener triggers."""
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
        raise WriteError("Daily write limit of 1 reached")

    monkeypatch.setattr(WeishauptHeatPump, "write", refuse)

    with pytest.raises(HomeAssistantError, match="limit") as raised:
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": 5},
            blocking=True,
        )
    assert raised.value.translation_key == "write_failed"
    assert entry.state is ConfigEntryState.LOADED


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


async def test_an_enabled_heating_circuit_is_polled(hass, pump):
    await _setup(hass, _entry(hass, data={**BASE_DATA, CONF.HK2: True}))

    assert any(
        read.address
        <= HEATING_CIRCUIT_2_ROOM_TEMPERATURE
        <= read.address + read.count - 1
        for read in pump.unit.read_events
    )
