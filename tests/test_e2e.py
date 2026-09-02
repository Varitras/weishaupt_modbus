"""End-to-end tests against a real Home Assistant instance.

These exercise the parts that only exist once Home Assistant itself is driving
the integration: entry setup and unload, the config-entry migration, and the
entity platforms. Everything below the coordinator (the Modbus wire) is faked -
the point here is the Home Assistant contract, not the client.

Marked `e2e` because each test boots a full Home Assistant instance; the
everyday run deselects them, CI runs them with `-m ""`.
"""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weishaupt_modbus.const import CONF, CONST
from custom_components.weishaupt_modbus.weishaupt_modbus_api.modbus_api import (
    WeishauptModbusClient,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(120)]

OUTSIDE_TEMPERATURE = 30001
OUTSIDE_TEMPERATURE_UNIQUE_ID = "weishaupt_wbbAussentemperatur"

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
def fake_modbus(monkeypatch):
    """The wire, replaced: connects at once, and every update hands out one
    outside temperature of 12.3 °C."""

    async def connect(self, startup=False):
        return True

    async def update(self):
        self.data = {OUTSIDE_TEMPERATURE: 123}
        return self.data

    monkeypatch.setattr(WeishauptModbusClient, "connect", connect)
    monkeypatch.setattr(WeishauptModbusClient, "update", update)
    # `connected` reads the real pymodbus transport, which never opens here.
    monkeypatch.setattr(WeishauptModbusClient, "connected", property(lambda self: True))
    disconnected = []

    async def disconnect(self):
        disconnected.append(self)

    monkeypatch.setattr(WeishauptModbusClient, "disconnect", disconnect)
    return disconnected


def _entry(hass, data=None, version=10):
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


async def test_setup_creates_all_three_platforms(hass):
    await _setup(hass, _entry(hass))

    registry = er.async_get(hass)
    domains = {entry.domain for entry in registry.entities.values()}
    assert {"sensor", "select", "number"} <= domains


async def test_unload_disconnects_the_modbus_client(hass, fake_modbus):
    entry = await _setup(hass, _entry(hass))

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert fake_modbus, "the Modbus connection was left open"


async def test_an_old_entry_migrates_to_the_current_version(hass):
    """A version-1 entry carries only the host; every later key has to be
    filled in, or the entity setup reads a key that is not there."""
    entry = _entry(hass, data={CONF.HOST: "192.0.2.10"}, version=1)

    await _setup(hass, entry)

    assert entry.version == 10
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

    assert entry.version == 10
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
    hass, fake_modbus, caplog
):
    """Issue #180: Home Assistant warns - and from 2026.12 refuses - when a
    flow schedules a reload itself while the entry also has an update
    listener, because that reloads twice. The listener is the one path here:
    the flow only updates the entry and aborts."""
    entry = await _setup(hass, _entry(hass))

    result = await hass.config_entries.flow.async_init(
        CONST.DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**BASE_DATA, CONF.HOST: "192.0.2.20"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF.HOST] == "192.0.2.20"
    assert len(fake_modbus) == 1, (
        f"the entry was reloaded {len(fake_modbus)} times; the update listener "
        "should reload it exactly once"
    )
    assert "has an update listener and should use it" not in caplog.text, (
        "Home Assistant reported the double-reload deprecation"
    )


async def test_the_options_flow_sets_the_poll_interval(hass, fake_modbus):
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
    assert entry.options == {CONST.OPTION_SCAN_INTERVAL: 60}
    assert len(fake_modbus) == 1, "the change was not applied by a reload"
    assert entry.runtime_data.coordinator.update_interval.total_seconds() == 60


async def test_an_out_of_range_poll_interval_is_refused(hass):
    entry = await _setup(hass, _entry(hass))

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONST.OPTION_SCAN_INTERVAL: 1}
        )


async def test_a_pump_that_refuses_the_first_connection_retries_later(
    hass, monkeypatch
):
    async def refuse(self, startup=False):
        return False

    monkeypatch.setattr(WeishauptModbusClient, "connect", refuse)
    monkeypatch.setattr(
        WeishauptModbusClient, "connected", property(lambda self: False)
    )
    entry = _entry(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
