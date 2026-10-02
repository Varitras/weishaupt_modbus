"""What a web interface value becomes as a sensor: reading, place and name."""

from types import SimpleNamespace

import pytest

from custom_components.weishaupt_modbus.const import CONF, CONST, DEVICES
from custom_components.weishaupt_modbus.webif.discovery import (
    HEAT_PUMP_PAGE,
    HEATING_PAGE,
    STATISTICS_PAGE,
)
from custom_components.weishaupt_modbus.webif_sensor import (
    REQUIRED_TITLES,
    WEBIF_SENSORS,
    WebifSensor,
    reading,
)

PUMP_DATA = {
    CONF.PREFIX: CONST.DEF_PREFIX,
    CONF.DEVICE_POSTFIX: "",
    CONF.NAME_DEVICE_PREFIX: False,
    CONF.NAME_TOPIC_PREFIX: False,
}


def _description(key):
    return next(sensor for sensor in WEBIF_SENSORS if sensor.key == key)


def _sensor(key, data):
    coordinator = SimpleNamespace(data=data, last_update_success=True)
    return WebifSensor(coordinator, PUMP_DATA, _description(key))


@pytest.mark.parametrize(
    ("key", "shown", "expected"),
    [
        ("hochdruck", "24.4 BAR", 24.4),
        ("hochdruck", "24.4 °C", None),
        ("hochdruck", None, None),
        ("ist_leistung", "4.9 KW", 4.9),
        ("ist_leistung", "Aus", 0.0),
        ("solltemperatur", "--", None),
        ("schaltspiele_verdichter", "13994", 13994.0),
        ("jaz_jahr", "4.16", 4.16),
        ("stellung_umschaltventil", "Warmwasser", "Warmwasser"),
        ("stellung_umschaltventil", "--", None),
    ],
)
def test_a_shown_value_is_a_reading_only_in_its_unit(key, shown, expected):
    """A value in another unit is another value: a page of another section,
    or a display the catalogue does not know."""
    assert reading(_description(key), shown) == expected


def test_every_page_requires_the_titles_of_its_sensors():
    """The value list: 31 values only the web interface has on the heat pump
    page, all 14 statistics, and the two heating settings."""
    assert {page: len(titles) for page, titles in REQUIRED_TITLES.items()} == {
        HEAT_PUMP_PAGE: 31,
        STATISTICS_PAGE: 14,
        HEATING_PAGE: 2,
    }
    assert len({sensor.key for sensor in WEBIF_SENSORS}) == len(WEBIF_SENSORS)


def test_a_sensor_shows_its_title_on_its_page():
    sensor = _sensor("hochdruck", {HEAT_PUMP_PAGE: {"Hochdruck": "24.4 BAR"}})

    assert sensor.available
    assert sensor.native_value == 24.4


def test_a_sensor_of_a_page_gone_is_unavailable():
    """The page failed twice in a row; its values are no longer shown."""
    sensor = _sensor("hochdruck", {HEAT_PUMP_PAGE: None})

    assert not sensor.available


@pytest.mark.parametrize(
    ("key", "device"),
    [
        ("hochdruck", DEVICES.WP),
        ("jaz_jahr", DEVICES.ST),
        ("leistungsbegrenzung_heizen", DEVICES.WP),
    ],
)
def test_a_sensor_sits_on_the_pumps_device_with_an_id_of_its_own(key, device):
    sensor = _sensor(key, {})

    assert sensor.unique_id == f"{CONST.DEF_PREFIX}webif_{key}"
    assert sensor.device_info["identifiers"] == {(CONST.DOMAIN, device)}
