"""The coordinator: what reaches the entities.

It maps the client's register cache onto the item list. Driven with a real
Home Assistant core (the `hass` fixture) and a fake client.
"""

from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weishaupt_modbus.const import CONF, CONST, DEVICES, TYPES
from custom_components.weishaupt_modbus.coordinator import (
    WeishauptModbusCoordinator,
    check_configured,
)
from custom_components.weishaupt_modbus.hpconst import (
    MODBUS_HZ2_ITEMS,
    MODBUS_SYS_ITEMS,
)
from custom_components.weishaupt_modbus.items import ModbusItem
from custom_components.weishaupt_modbus.weishaupt_modbus_api.exceptions import (
    ConnectionFailedError,
)
from homeassistant import config_entries
from homeassistant.helpers.update_coordinator import UpdateFailed

OUTSIDE_TEMPERATURE = 30001


def _entry(hass, **overrides):
    data = {
        CONF.HOST: "127.0.0.1",
        CONF.HK2: False,
        CONF.HK3: False,
        CONF.HK4: False,
        CONF.HK5: False,
    }
    data.update(overrides)
    entry = MockConfigEntry(domain=CONST.DOMAIN, data=data)
    entry.add_to_hass(hass)
    return entry


class FakeClient:
    def __init__(self, data=None, fail=None):
        self.data = data or {}
        self.fail = fail
        self.connected = True
        self.updates = 0

    async def update(self):
        self.updates += 1
        if self.fail:
            raise self.fail
        return self.data

    def get_value(self, address):
        return self.data.get(address)


def _modbus_coordinator(hass, entry, client, items):
    token = config_entries.current_entry.set(entry)
    try:
        return WeishauptModbusCoordinator(
            hass=hass, client=client, api_items=items, p_config_entry=entry
        )
    finally:
        config_entries.current_entry.reset(token)


# --- Modbus ---------------------------------------------------------------


async def test_the_cached_register_value_reaches_the_item(hass):
    entry = _entry(hass)
    client = FakeClient({OUTSIDE_TEMPERATURE: 123})
    items = [item for item in MODBUS_SYS_ITEMS if item.address == OUTSIDE_TEMPERATURE]
    coordinator = _modbus_coordinator(hass, entry, client, items)

    result = await coordinator._async_update_data()

    assert result[items[0].translation_key] == 123
    assert items[0].state == 123
    assert client.updates == 1


async def test_unconfigured_circuit_items_are_not_published(hass):
    """Heating circuits 2-5 are optional; their items stay out of the result
    until the entry enables them."""
    entry = _entry(hass, **{CONF.HK2: False})
    client = FakeClient({item.address: 1 for item in MODBUS_HZ2_ITEMS})
    coordinator = _modbus_coordinator(hass, entry, client, list(MODBUS_HZ2_ITEMS))

    result = await coordinator._async_update_data()

    assert result == {}


async def test_a_configured_circuit_is_published(hass):
    entry = _entry(hass, **{CONF.HK2: True})
    client = FakeClient({item.address: 1 for item in MODBUS_HZ2_ITEMS})
    coordinator = _modbus_coordinator(hass, entry, client, list(MODBUS_HZ2_ITEMS))

    result = await coordinator._async_update_data()

    assert result, "the enabled circuit published nothing"


async def test_calculated_sensor_is_not_polled_and_reads_as_none(hass):
    """A calculated sensor evaluates in memory; its address is only a place
    in the table, and the cache value behind it belongs to another item."""
    entry = _entry(hass)
    calculated = ModbusItem(
        OUTSIDE_TEMPERATURE, "calc", "number", TYPES.SENSOR_CALC, DEVICES.SYS, "calc"
    )
    client = FakeClient({OUTSIDE_TEMPERATURE: 123})
    coordinator = _modbus_coordinator(hass, entry, client, [calculated])

    result = await coordinator._async_update_data()

    assert result == {"calc": None}
    assert calculated.state is None


async def test_communication_failure_is_update_failed(hass):
    """The coordinator contract: transport trouble is UpdateFailed, so the
    entities go unavailable instead of the update loop dying."""
    entry = _entry(hass)
    client = FakeClient(fail=ConnectionFailedError("down"))
    coordinator = _modbus_coordinator(hass, entry, client, [])

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_a_value_is_looked_up_by_translation_key(hass):
    entry = _entry(hass)
    item = ModbusItem(
        30001, "x", "temperature", TYPES.SENSOR, DEVICES.SYS, "aussentemp"
    )
    item.state = 42
    coordinator = _modbus_coordinator(hass, entry, FakeClient(), [item])

    assert coordinator.get_value_from_item("aussentemp") == 42
    assert coordinator.get_value_from_item("nothing") is None


@pytest.mark.parametrize(
    ("device", "key", "enabled", "expected"),
    [
        (DEVICES.HZ2, CONF.HK2, True, True),
        (DEVICES.HZ2, CONF.HK2, False, False),
        (DEVICES.HZ5, CONF.HK5, False, False),
        (DEVICES.SYS, CONF.HK2, False, True),
    ],
)
async def test_check_configured_follows_the_circuit_switches(
    device, key, enabled, expected
):
    item = ModbusItem(1, "x", "number", TYPES.SENSOR, device, "k")
    entry = SimpleNamespace(data={key: enabled})

    assert await check_configured(item, entry) is expected
