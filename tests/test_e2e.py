"""End-to-end tests against a real Home Assistant instance.

These exercise the parts that only exist once Home Assistant itself is driving
the integration: entry setup and unload, the config-entry migration, and the
entity platforms. Everything below the coordinator (the Modbus wire) is faked -
the point here is the Home Assistant contract, not the client.

Marked `e2e` because each test boots a full Home Assistant instance; the
everyday run deselects them, CI runs them with `-m ""`.
"""

from modbus_connection import ModbusConnectionError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weishaupt_modbus.const import CONF, CONST
from custom_components.weishaupt_modbus.weishaupt_modbus_api.const import DEFAULT_PORT
from custom_components.weishaupt_modbus.weishaupt_modbus_api.device import (
    WeishauptHeatPump,
)
from custom_components.weishaupt_modbus.weishaupt_modbus_api.exceptions import (
    WriteError,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(120)]

OUTSIDE_TEMPERATURE = 30001
OUTSIDE_TEMPERATURE_UNIQUE_ID = "weishaupt_wbbAussentemperatur"
SYSTEM_OPERATION_MODE = 40001
SYSTEM_OPERATION_MODE_UNIQUE_ID = "weishaupt_wbbSystembetriebsart"
COMFORT_ROOM_TEMPERATURE = 41105
COMFORT_ROOM_TEMPERATURE_UNIQUE_ID = "weishaupt_wbbRaumsolltemperatur Komfort"
SUMMER = 3

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
    assert entry.runtime_data.device.write_budget.total == 1


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

    pump = entry.runtime_data.device
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
    assert entry.runtime_data.device.write_budget.total == 1, (
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

    async def refuse(self, item, value):
        raise WriteError("Daily write limit of 1 reached")

    monkeypatch.setattr(WeishauptHeatPump, "write", refuse)

    with pytest.raises(HomeAssistantError, match="limit"):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": 5},
            blocking=True,
        )
    assert entry.state is ConfigEntryState.LOADED


async def test_a_pump_that_refuses_the_first_connection_retries_later(hass, pump):
    pump.fail_requests(ModbusConnectionError("connection refused"))
    entry = _entry(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


HEATING_CIRCUIT_2_ROOM_TEMPERATURE = 31202


async def test_a_disabled_heating_circuit_is_neither_polled_nor_an_entity(hass, pump):
    """Audit P2-03: circuits 2-5 were read on every poll and registered as
    entities showing unknown, whatever the entry said."""
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
