"""The entity layer: names, units, value translation and writes.

Driven without Home Assistant's state machine - a fake coordinator hands out
values and records writes, and the entity's own translation is what is
asserted.
"""

from types import SimpleNamespace

import pytest

from custom_components.weishaupt_modbus import entities
from custom_components.weishaupt_modbus.const import CONF, DEVICES, FORMATS, TYPES
from custom_components.weishaupt_modbus.items import ModbusItem
from custom_components.weishaupt_modbus.weishaupt_modbus_api.calculations import (
    performance_factor,
)
from custom_components.weishaupt_modbus.weishaupt_modbus_api.hpconst import (
    PARAMS_CALCPOWER,
    PARAMS_CALCSPREIZUNG,
    SYS_BETRIEBSART,
)
from homeassistant.exceptions import ServiceValidationError


def _entry(prefix="weishaupt_wbb", postfix="", device_prefix=False, topic_prefix=False):
    return SimpleNamespace(
        data={
            CONF.PREFIX: prefix,
            CONF.DEVICE_POSTFIX: postfix,
            CONF.NAME_DEVICE_PREFIX: device_prefix,
            CONF.NAME_TOPIC_PREFIX: topic_prefix,
            CONF.PORT: 502,
        },
        runtime_data=SimpleNamespace(powermap=None),
    )


class FakeCoordinator:
    def __init__(self, values=None, cache=None):
        self.values = values or {}
        self.device = SimpleNamespace(
            value_of=(cache or {}).get,
            write=self._write,
            write_off=self._write_off,
        )
        self.writes: list = []
        self.data = None
        self.last_update_success = True

    async def _write(self, item, value):
        self.writes.append((item.address, value))
        item.state = value  # what the real device does after the wire confirmed

    async def _write_off(self, item):
        self.writes.append((item.address, "off"))

    def async_update_listeners(self):
        pass

    def get_value_from_item(self, key):
        return self.values.get(key)


def _temperature(params=None, address=30001):
    return ModbusItem(
        address,
        "Aussentemperatur",
        FORMATS.TEMPERATURE,
        TYPES.SENSOR,
        DEVICES.SYS,
        "aussentemp",
        params=params or {"unit": "°C", "divider": 10, "precision": 1},
    )


def test_a_row_without_params_is_a_whole_number_without_a_unit():
    """Counters and configuration codes carry no params. They used to get an
    empty-string unit and two decimals: operating hours read 12.00 h."""
    item = ModbusItem(
        34103, "Schaltspiele 2. WEZ", FORMATS.NUMBER, TYPES.SENSOR, DEVICES.W2, "x"
    )
    sensor = entities.MySensorEntity(_entry(), item, FakeCoordinator(), 0)

    assert sensor._attr_native_unit_of_measurement is None
    assert sensor._attr_suggested_display_precision == 0


# --- bounds -------------------------------------------------------------------


def _dhw_normal(coordinator):
    item = ModbusItem(
        42103,
        "Warmwasser Normal",
        FORMATS.TEMPERATURE,
        TYPES.NUMBER,
        DEVICES.WW,
        "ww_normal",
        params={
            "unit": "°C",
            "divider": 10,
            "precision": 1,
            "min": 20,
            "max": 80,
            "dynamic_min": "ww_absenk",
        },
    )
    return entities.MyNumberEntity(_entry(), item, coordinator, 0)


def test_a_related_setpoint_narrows_the_bounds_but_never_widens_them():
    """DHW lowering may sit at 10 degC; that used to become the minimum of
    DHW normal, whose own floor is 20 degC, and 15 degC reached the wire."""
    number = _dhw_normal(FakeCoordinator(values={"ww_absenk": 100}))
    number.set_min_max(True)
    assert number.native_min_value == 20

    number = _dhw_normal(FakeCoordinator(values={"ww_absenk": 350}))
    number.set_min_max(True)
    assert number.native_min_value == 35


async def test_a_value_outside_the_current_bounds_is_refused_at_the_write():
    """Home Assistant validates against the bounds it was told a poll ago; a
    related setpoint written since has moved them."""
    coordinator = FakeCoordinator(values={"ww_absenk": 350})
    number = _dhw_normal(coordinator)

    with pytest.raises(ServiceValidationError):
        await number.set_translate_val(30)

    assert coordinator.writes == []
    assert await number.set_translate_val(40) == 400


# --- the off switch ----------------------------------------------------------


def _switchable_setpoint():
    return ModbusItem(
        41111,
        "Heizen Konstanttemp Absenk",
        FORMATS.TEMPERATURE,
        TYPES.NUMBER,
        DEVICES.HZ,
        "heiz_konstanttemp_absenk",
        params={
            "unit": "°C",
            "divider": 10,
            "precision": 1,
            "min": 7,
            "max": 66,
            "off_is_a_setting": True,
        },
    )


def test_the_switch_reads_the_setpoints_off_state():
    item = _switchable_setpoint()
    switch = entities.MySetpointSwitchEntity(_entry(), item, FakeCoordinator(), 0)
    assert switch.is_on is True
    assert switch.unique_id == "weishaupt_wbbHeizen Konstanttemp Absenk_active"

    item.is_off = True
    switch.async_write_ha_state = lambda: None
    switch._handle_coordinator_update()

    assert switch.is_on is False


async def test_turning_off_writes_the_off_word_and_on_restores_the_value():
    item = _switchable_setpoint()
    item.last_setting = 185
    coordinator = FakeCoordinator()
    switch = entities.MySetpointSwitchEntity(_entry(), item, coordinator, 0)
    coordinator.async_update_listeners = lambda: None

    await switch.async_turn_off()
    await switch.async_turn_on()

    assert coordinator.writes == [(41111, "off"), (41111, 185)]


async def test_turning_on_a_setpoint_never_seen_on_uses_the_minimum():
    item = _switchable_setpoint()
    coordinator = FakeCoordinator()
    switch = entities.MySetpointSwitchEntity(_entry(), item, coordinator, 0)
    coordinator.async_update_listeners = lambda: None

    await switch.async_turn_on()

    assert coordinator.writes == [(41111, 70)], "7.0 degC, the range's minimum"


def test_a_number_stays_available_while_its_setpoint_is_off():
    item = _switchable_setpoint()
    item.is_off = True
    number = entities.MyNumberEntity(_entry(), item, FakeCoordinator(), 0)

    assert number.available is True
    assert number.native_value is None


# --- availability -----------------------------------------------------------


def test_an_absent_sensor_makes_the_entity_unavailable():
    """The device marks a row invalid for the no-sensor word and for a refused
    band; until now nothing read that flag and the entity showed unknown,
    as if a connected sensor had no reading."""
    item = _temperature()
    sensor = entities.MySensorEntity(_entry(), item, FakeCoordinator(), 0)
    assert sensor.available is True

    item.is_invalid = True

    assert sensor.available is False


def test_a_failed_refresh_makes_the_entity_unavailable_too():
    coordinator = FakeCoordinator()
    sensor = entities.MySensorEntity(_entry(), _temperature(), coordinator, 0)
    coordinator.last_update_success = False

    assert sensor.available is False


# --- naming ---------------------------------------------------------------


def test_the_name_prefix_is_empty_by_default():
    sensor = entities.MySensorEntity(_entry(), _temperature(), FakeCoordinator(), 0)

    assert sensor._attr_translation_placeholders == {"prefix": ""}


def test_device_prefix_in_name_when_enabled():
    sensor = entities.MySensorEntity(
        _entry(device_prefix=True), _temperature(), FakeCoordinator(), 0
    )

    assert sensor._attr_translation_placeholders == {"prefix": "weishaupt_wbb_"}


def test_topic_prefix_names_the_device_group():
    sensor = entities.MySensorEntity(
        _entry(topic_prefix=True), _temperature(), FakeCoordinator(), 0
    )

    assert sensor._attr_translation_placeholders == {"prefix": "SYS_"}


def test_both_prefixes_stack_topic_first():
    sensor = entities.MySensorEntity(
        _entry(device_prefix=True, topic_prefix=True),
        _temperature(),
        FakeCoordinator(),
        0,
    )

    assert sensor._attr_translation_placeholders == {"prefix": "SYS_weishaupt_wbb_"}


def test_the_unique_id_carries_prefix_name_and_postfix():
    sensor = entities.MySensorEntity(
        _entry(postfix="keller"), _temperature(), FakeCoordinator(), 0
    )

    assert sensor._attr_unique_id == "weishaupt_wbbAussentemperatur_keller"


def test_the_device_identifier_carries_the_postfix():
    sensor = entities.MySensorEntity(
        _entry(postfix="keller"), _temperature(), FakeCoordinator(), 0
    )

    assert sensor.device_info["identifiers"] == {
        ("weishaupt_modbus", "dev_system_keller")
    }


# --- units and values -----------------------------------------------------


def test_unit_step_and_divider_come_from_the_params():
    sensor = entities.MySensorEntity(_entry(), _temperature(), FakeCoordinator(), 0)

    assert sensor._attr_native_unit_of_measurement == "°C"
    assert sensor._divider == 10
    assert sensor._attr_suggested_display_precision == 1


def test_a_raw_value_is_divided_for_display():
    sensor = entities.MySensorEntity(_entry(), _temperature(), FakeCoordinator(), 0)

    assert sensor.translate_val(235) == 23.5
    assert sensor.translate_val(None) is None


def test_status_state_is_the_translation_key_not_the_text():
    """Templates compare against `states()`, and that is the key."""
    item = ModbusItem(
        40001,
        "Systembetriebsart",
        FORMATS.STATUS,
        TYPES.SELECT,
        DEVICES.SYS,
        "sys_operationmode",
        resultlist=SYS_BETRIEBSART,
    )
    select = entities.MySelectEntity(_entry(), item, FakeCoordinator(), 0)

    assert select.translate_val(SYS_BETRIEBSART[0].number) == (
        SYS_BETRIEBSART[0].translation_key
    )


def test_an_unknown_status_number_is_reported_not_hidden():
    item = ModbusItem(
        40001,
        "x",
        FORMATS.STATUS,
        TYPES.SELECT,
        DEVICES.SYS,
        "k",
        resultlist=SYS_BETRIEBSART,
    )
    select = entities.MySelectEntity(_entry(), item, FakeCoordinator(), 0)

    assert select.translate_val(9999) == "unbekannt <9999>"


def test_select_options_are_the_status_translation_keys():
    item = ModbusItem(
        40001,
        "x",
        FORMATS.STATUS,
        TYPES.SELECT,
        DEVICES.SYS,
        "k",
        resultlist=SYS_BETRIEBSART,
    )
    select = entities.MySelectEntity(_entry(), item, FakeCoordinator(), 0)

    assert select._attr_options == [
        status.translation_key for status in SYS_BETRIEBSART
    ]


def test_dynamic_limits_are_read_from_the_sibling_items():
    params = {
        "unit": "°C",
        "divider": 10,
        "dynamic_min": "low",
        "dynamic_max": "high",
    }
    item = ModbusItem(
        41001, "x", FORMATS.TEMPERATURE, TYPES.NUMBER, DEVICES.HZ, "k", params=params
    )
    number = entities.MyNumberEntity(
        _entry(), item, FakeCoordinator(values={"low": 150, "high": 300}), 0
    )

    assert number._attr_native_min_value == 15.0
    assert number._attr_native_max_value == 30.0


# --- writes ---------------------------------------------------------------


async def test_a_number_write_is_scaled_by_the_divider():
    item = ModbusItem(
        41001,
        "x",
        FORMATS.TEMPERATURE,
        TYPES.NUMBER,
        DEVICES.HZ,
        "k",
        params={"unit": "°C", "divider": 10},
    )
    coordinator = FakeCoordinator()
    number = entities.MyNumberEntity(_entry(), item, coordinator, 0)
    number.async_write_ha_state = lambda: None

    await number.async_set_native_value(21.5)
    number._handle_coordinator_update()  # what async_update_listeners triggers

    assert coordinator.writes == [(41001, 215)]
    assert number._attr_native_value == 21.5


async def test_a_select_write_sends_the_number_behind_the_key():
    item = ModbusItem(
        40001,
        "x",
        FORMATS.STATUS,
        TYPES.SELECT,
        DEVICES.SYS,
        "k",
        resultlist=SYS_BETRIEBSART,
    )
    coordinator = FakeCoordinator()
    select = entities.MySelectEntity(_entry(), item, coordinator, 0)
    select.async_write_ha_state = lambda: None
    chosen = SYS_BETRIEBSART[1]

    await select.async_select_option(chosen.translation_key)
    select._handle_coordinator_update()  # what async_update_listeners triggers

    assert coordinator.writes == [(40001, chosen.number)]
    assert select._attr_current_option == chosen.translation_key


async def test_an_unknown_option_is_not_written():
    item = ModbusItem(
        40001,
        "x",
        FORMATS.STATUS,
        TYPES.SELECT,
        DEVICES.SYS,
        "k",
        resultlist=SYS_BETRIEBSART,
    )
    coordinator = FakeCoordinator()
    select = entities.MySelectEntity(_entry(), item, coordinator, 0)

    await select.async_select_option("no_such_option")

    assert coordinator.writes == []


# --- calculated sensors ---------------------------------------------------


def test_a_calculated_sensor_evaluates_its_formula_over_sibling_values():
    """Spreizung = flow - return: val_0 is this item's own register (already
    divided), val_1 the sibling looked up by translation key (raw)."""
    item = ModbusItem(
        33111,
        "Spreizung",
        FORMATS.TEMPERATURE,
        TYPES.SENSOR_CALC,
        DEVICES.WP,
        "spreizung",
        params=PARAMS_CALCSPREIZUNG,
    )
    coordinator = FakeCoordinator(values={"rl_temp": 300}, cache={33111: 350})
    sensor = entities.MyCalcSensorEntity(_entry(), item, coordinator, 0)

    assert sensor.translate_val(None) == pytest.approx(5.0)


def test_a_calculated_sensor_without_a_formula_reads_as_none():
    item = ModbusItem(
        33111,
        "x",
        FORMATS.NUMBER,
        TYPES.SENSOR_CALC,
        DEVICES.WP,
        "k",
        params={"unit": "W"},
    )
    sensor = entities.MyCalcSensorEntity(_entry(), item, FakeCoordinator(), 0)

    assert sensor.translate_val(None) is None


def test_a_formula_with_an_absent_operand_reads_as_none():
    """The return temperature register answered the no-sensor sentinel, so
    the sibling value is None. The entity raised out of async_added_to_hass
    and Home Assistant refused it for good - every calculated sensor of a
    pump with one absent register was missing after setup."""
    item = ModbusItem(
        33111,
        "Spreizung",
        FORMATS.TEMPERATURE,
        TYPES.SENSOR_CALC,
        DEVICES.WP,
        "spreizung",
        params=PARAMS_CALCSPREIZUNG,
    )
    coordinator = FakeCoordinator(values={"rl_temp": None}, cache={33111: 350})
    sensor = entities.MyCalcSensorEntity(_entry(), item, coordinator, 0)

    assert sensor.translate_val(None) is None


def test_a_formula_without_a_value_reads_as_none_not_zero():
    """performance_factor with 0 kWh electric energy - the first poll of a
    new day - used to publish a coefficient of 0.0."""
    item = ModbusItem(
        33111,
        "x",
        FORMATS.NUMBER,
        TYPES.SENSOR_CALC,
        DEVICES.WP,
        "k",
        params={"unit": "W", "precision": 0, "calculation": lambda own: None},
    )
    sensor = entities.MyCalcSensorEntity(
        _entry(), item, FakeCoordinator(cache={33111: 0}), 0
    )

    assert sensor.translate_val(None) is None


def test_the_heat_output_is_unknown_without_a_power_map():
    item = ModbusItem(
        33103,
        "Wärmeleistung",
        FORMATS.NUMBER,
        TYPES.SENSOR_CALC,
        DEVICES.WP,
        "waermeleistung",
        params=PARAMS_CALCPOWER,
    )
    coordinator = FakeCoordinator(
        values={"luftansautgemp": 100, "vl_temp": 350}, cache={33103: 50}
    )
    entry = _entry()
    entry.runtime_data.powermap = SimpleNamespace(map=lambda outside, flow: None)
    sensor = entities.MyCalcSensorEntity(entry, item, coordinator, 0)

    assert sensor.translate_val(None) is None


def test_a_calculated_sensor_whose_own_register_is_absent_reads_as_none():
    """0.0 stood in for a missing own register: the spread of an absent
    supply temperature and a 30 °C return read as -30 °C, a plausible
    number automations acted on."""
    item = ModbusItem(
        33111,
        "Spreizung",
        FORMATS.TEMPERATURE,
        TYPES.SENSOR_CALC,
        DEVICES.WP,
        "spreizung",
        params=PARAMS_CALCSPREIZUNG,
    )
    coordinator = FakeCoordinator(values={"rl_temp": 300}, cache={})
    sensor = entities.MyCalcSensorEntity(_entry(), item, coordinator, 0)

    assert sensor.translate_val(None) is None


def test_the_heat_output_takes_the_power_map():
    item = ModbusItem(
        33103,
        "Wärmeleistung",
        FORMATS.NUMBER,
        TYPES.SENSOR_CALC,
        DEVICES.WP,
        "waermeleistung",
        params=PARAMS_CALCPOWER,
    )
    coordinator = FakeCoordinator(
        values={"luftansautgemp": 100, "vl_temp": 350}, cache={33103: 50}
    )
    entry = _entry()
    entry.runtime_data.powermap = SimpleNamespace(map=lambda outside, flow: 8000.0)
    sensor = entities.MyCalcSensorEntity(entry, item, coordinator, 0)

    assert sensor.translate_val(None) == 4000


@pytest.mark.parametrize(
    ("value", "divider", "word"),
    [
        (1.15, 100, 115),
        (1.14, 100, 114),
        (-0.5, 10, -5),
        (22.5, 10, 225),
        (0.29, 100, 29),
    ],
)
def test_a_user_value_becomes_the_nearest_register_word(value, divider, word):
    """int(1.15 * 100) is 114: the heating curve the user set was written one
    step too low, silently."""
    assert entities.to_register_value(value, divider) == word


def test_the_performance_factor_is_undefined_without_electric_energy():
    assert performance_factor(12.0, 0) is None
    assert performance_factor(12.0, 4) == 3.0
