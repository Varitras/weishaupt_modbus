"""The coordinator: what reaches the entities.

It maps the client's register cache onto the item list. Driven with a real
Home Assistant core (the `hass` fixture) and a fake client.
"""

import ast
import asyncio
import copy
import inspect
import logging
import textwrap
from types import SimpleNamespace

from modbus_connection import ModbusConnectionError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weishaupt_modbus.const import CONF, CONST, DEVICES, TYPES
from custom_components.weishaupt_modbus.coordinator import (
    WeishauptModbusCoordinator,
    check_configured,
    scan_interval,
    write_budget,
)
from custom_components.weishaupt_modbus.items import ModbusItem
from custom_components.weishaupt_modbus.weishaupt_modbus_api.hpconst import (
    MODBUS_SYS_ITEMS,
)
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


class FakeDevice:
    def __init__(self, items, data=None, fail=None):
        self.items = items
        self.data = data or {}
        self.fail = fail
        self.updates = 0

    async def async_update(self):
        self.updates += 1
        if self.fail:
            raise self.fail
        for item in self.items:
            item.state = self.data.get(item.address)


def _modbus_coordinator(hass, entry, device, items):
    return WeishauptModbusCoordinator(
        hass=hass, device=device, api_items=items, config_entry=entry
    )


def test_the_coordinator_knows_its_entry_outside_the_setup_context(hass):
    """The entry reached the coordinator only through Home Assistant's setup
    context variable; built anywhere else, the coordinator had none."""
    entry = _entry(hass)

    coordinator = WeishauptModbusCoordinator(
        hass=hass, device=FakeDevice([]), api_items=[], config_entry=entry
    )

    assert coordinator.config_entry is entry


# --- Modbus ---------------------------------------------------------------


async def test_the_cached_register_value_reaches_the_item(hass):
    entry = _entry(hass)
    # A copy: the rows carry the last poll's state, and a test that writes
    # into the module's own table leaves it there for every later test.
    items = [
        copy.deepcopy(item)
        for item in MODBUS_SYS_ITEMS
        if item.address == OUTSIDE_TEMPERATURE
    ]
    client = FakeDevice(items, {OUTSIDE_TEMPERATURE: 123})
    coordinator = _modbus_coordinator(hass, entry, client, items)

    result = await coordinator._async_update_data()

    assert result[items[0].translation_key] == 123
    assert items[0].state == 123
    assert client.updates == 1


async def test_calculated_sensor_is_not_polled_and_reads_as_none(hass):
    """A calculated sensor evaluates in memory; its address is only a place
    in the table, and the cache value behind it belongs to another item."""
    entry = _entry(hass)
    calculated = ModbusItem(
        OUTSIDE_TEMPERATURE, "calc", "number", TYPES.SENSOR_CALC, DEVICES.SYS, "calc"
    )
    client = FakeDevice([], {OUTSIDE_TEMPERATURE: 123})
    coordinator = _modbus_coordinator(hass, entry, client, [calculated])

    result = await coordinator._async_update_data()

    assert result == {"calc": None}
    assert calculated.state is None


async def test_communication_failure_is_update_failed(hass, caplog):
    """The coordinator contract: transport trouble is UpdateFailed, so the
    entities go unavailable instead of the update loop dying. Its own text
    says what went wrong, and it is no fault to log a traceback for."""
    entry = _entry(hass)
    client = FakeDevice([], fail=ModbusConnectionError("down"))
    coordinator = _modbus_coordinator(hass, entry, client, [])

    with pytest.raises(UpdateFailed) as raised:
        await coordinator._async_update_data()

    assert raised.value.translation_placeholders == {"error": "down"}
    assert not [record for record in caplog.records if record.exc_info]


async def test_a_poll_that_timed_out_names_its_kind(hass):
    """A band that hangs ends in a bare TimeoutError, whose text is empty:
    the notice read "Communication failed: " with nothing after it."""
    client = FakeDevice([], fail=TimeoutError())
    coordinator = _modbus_coordinator(hass, _entry(hass), client, [])

    with pytest.raises(UpdateFailed) as raised:
        await coordinator._async_update_data()

    assert raised.value.translation_placeholders == {"error": "TimeoutError"}


async def test_three_failed_polls_keep_the_last_values_the_fourth_does_not(hass):
    """A controller that misses one poll took every entity to unavailable
    for a scan interval and back; automations on unavailable fired on every
    hiccup. Three polls of grace, never before the first values exist."""
    entry = _entry(hass)
    item = copy.deepcopy(
        next(i for i in MODBUS_SYS_ITEMS if i.address == OUTSIDE_TEMPERATURE)
    )
    device = FakeDevice([item], data={OUTSIDE_TEMPERATURE: 123})
    coordinator = _modbus_coordinator(hass, entry, device, [item])
    coordinator.data = await coordinator._async_update_data()
    assert coordinator.data[item.translation_key] == 123

    device.fail = ModbusConnectionError("down")
    for _ in range(3):
        assert (await coordinator._async_update_data())[item.translation_key] == 123
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    device.fail = None
    coordinator.data = await coordinator._async_update_data()
    device.fail = ModbusConnectionError("down again")

    assert (await coordinator._async_update_data())[item.translation_key] == 123, (
        "a good poll resets the count"
    )


def _polled_once(hass):
    """A coordinator that has its first values, on a device of one item."""
    item = copy.deepcopy(
        next(i for i in MODBUS_SYS_ITEMS if i.address == OUTSIDE_TEMPERATURE)
    )
    device = FakeDevice([item], data={OUTSIDE_TEMPERATURE: 123})
    return _modbus_coordinator(hass, _entry(hass), device, [item]), device, item


async def test_an_unexpected_error_counts_as_a_failed_poll(hass):
    """An error that is no link's, a fault of the decoding say, passed the
    grace polls: every entity went unavailable at once."""
    coordinator, device, item = _polled_once(hass)
    coordinator.data = await coordinator._async_update_data()
    device.fail = RuntimeError("192.0.2.10 sent nothing to decode")

    for _ in range(3):
        assert (await coordinator._async_update_data())[item.translation_key] == 123
    with pytest.raises(UpdateFailed) as raised:
        await coordinator._async_update_data()

    assert raised.value.translation_placeholders == {"error": "RuntimeError"}


async def test_a_cancelled_poll_goes_through_uncounted(hass):
    """The entry unloading or Home Assistant stopping cancels a poll. The
    catch for faults of this code must not take that for a failed poll."""
    coordinator, device, _ = _polled_once(hass)
    coordinator.data = await coordinator._async_update_data()
    device.fail = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await coordinator._async_update_data()

    assert coordinator.failed_polls == 0


async def test_an_unexpected_error_in_a_poll_logs_its_traceback_once(hass, caplog):
    """Home Assistant logged one on every poll, as an error."""
    coordinator, device, _ = _polled_once(hass)
    coordinator.data = await coordinator._async_update_data()
    device.fail = RuntimeError("a fault of the decoding")

    for _ in range(6):
        await coordinator.async_refresh()

    tracebacks = [
        record
        for record in caplog.records
        if record.exc_info and record.levelno >= logging.ERROR
    ]
    assert len(tracebacks) == 1


async def test_a_value_is_looked_up_by_translation_key(hass):
    entry = _entry(hass)
    item = ModbusItem(
        30001, "x", "temperature", TYPES.SENSOR, DEVICES.SYS, "aussentemp"
    )
    item.state = 42
    coordinator = _modbus_coordinator(hass, entry, FakeDevice([item]), [item])

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

    assert check_configured(item, entry) is expected


def test_the_poll_interval_defaults_and_follows_the_option():
    assert scan_interval(SimpleNamespace(options={})) == CONST.SCAN_INTERVAL
    assert (
        scan_interval(
            SimpleNamespace(options={CONST.OPTION_SCAN_INTERVAL: 45})
        ).total_seconds()
        == 45
    )


def test_the_write_thresholds_default_and_follow_the_options():
    default = write_budget(SimpleNamespace(options={}))
    assert (default.warn_at, default.limit) == (50, 0)

    chosen = write_budget(
        SimpleNamespace(
            options={
                CONST.OPTION_WRITE_WARNING_PER_DAY: 10,
                CONST.OPTION_WRITE_LIMIT_PER_DAY: 20,
            }
        )
    )
    assert (chosen.warn_at, chosen.limit) == (10, 20)


TIME_LIMITS = {"timeout", "timeout_at", "wait_for"}


def _called(call: ast.Call) -> str:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return ""


def test_the_poll_sets_no_time_limit_of_its_own():
    """The limit sits on each band, inside the controller's lock. One around
    the whole poll counted the waits for the web interface as well, and timed
    a healthy poll out while the web interface held the lock."""
    poll = ast.parse(
        textwrap.dedent(
            inspect.getsource(WeishauptModbusCoordinator._async_update_data)
        )
    )

    limits = [
        ast.unparse(node)
        for node in ast.walk(poll)
        if isinstance(node, ast.Call) and _called(node) in TIME_LIMITS
    ]

    assert limits == []
