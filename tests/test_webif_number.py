"""The heating power limit as a number on the web interface's device."""

from types import SimpleNamespace

import pytest

from custom_components.weishaupt_modbus.const import CONF, CONST, DEVICES
from custom_components.weishaupt_modbus.webif.discovery import HEATING_PAGE
from custom_components.weishaupt_modbus.webif_number import PowerLimitNumber
from custom_components.weishaupt_modbus.webif_sensor import (
    REQUIRED_TITLES,
    WEBIF_SENSORS,
)
from homeassistant.components.number import NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.exceptions import ServiceValidationError

PUMP_DATA = {
    CONF.PREFIX: CONST.DEF_PREFIX,
    CONF.DEVICE_POSTFIX: "",
    CONF.NAME_DEVICE_PREFIX: False,
    CONF.NAME_TOPIC_PREFIX: False,
}


class FakeCoordinator(SimpleNamespace):
    """The heating page's last values, and the limits it was asked to write."""

    def __init__(self, heating):
        super().__init__(
            data={HEATING_PAGE: heating}, last_update_success=True, written=[]
        )

    async def set_power_limit(self, target):
        self.written.append(target)


def _number(heating):
    return PowerLimitNumber(FakeCoordinator(heating), PUMP_DATA)


def test_the_number_shows_the_heating_page_value():
    number = _number({"Leistungsbegrenzung": "60 %", "Schaltdifferenz": "4.5 K"})

    assert number.available
    assert number.native_value == 60


def test_the_number_of_a_heating_page_gone_is_unavailable():
    """The page failed twice in a row; its values are no longer shown."""
    assert not _number(None).available


def test_the_number_is_a_config_box_from_10_to_100():
    """The controller's own list offers 10 to 100 in steps of one."""
    number = _number({})

    assert (number.native_min_value, number.native_max_value) == (10, 100)
    assert number.native_step == 1
    assert number.mode is NumberMode.BOX
    assert number.native_unit_of_measurement == PERCENTAGE
    assert number.entity_category is EntityCategory.CONFIG
    assert number.unique_id == f"{CONST.DEF_PREFIX}webif_leistungsbegrenzung_heizen"
    assert number.device_info["identifiers"] == {(CONST.DOMAIN, DEVICES.WEBIF)}
    assert number.translation_key == "webif_leistungsbegrenzung_heizen"


async def test_setting_the_number_asks_the_coordinator():
    number = _number({"Leistungsbegrenzung": "60 %"})

    await number.async_set_native_value(61.0)

    assert number.coordinator.written == [61]


async def test_a_value_between_two_percentages_is_refused():
    """Home Assistant checks the range only; the controller offers whole
    percentages, and rounding would write a value nobody asked for."""
    number = _number({"Leistungsbegrenzung": "60 %"})

    with pytest.raises(ServiceValidationError) as refused:
        await number.async_set_native_value(60.5)

    assert refused.value.translation_key == "webif_power_limit_whole_percent"
    assert refused.value.translation_placeholders == {"value": "60.5"}
    assert number.coordinator.written == []


def test_the_heating_page_still_requires_the_power_limit():
    """The number reads it from the heating page's reading, which keeps only
    the titles the page requires."""
    assert REQUIRED_TITLES[HEATING_PAGE] == {"Leistungsbegrenzung", "Schaltdifferenz"}
    assert "leistungsbegrenzung_heizen" not in {sensor.key for sensor in WEBIF_SENSORS}
