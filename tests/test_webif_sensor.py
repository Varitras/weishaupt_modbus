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
from homeassistant.components.sensor import SensorDeviceClass

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
        ("druckgastemperatur", "--", None),
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


# What the controller shows for an idle power or speed, and for a setpoint
# while nothing is demanded.
IDLE_SHOWN_AS_AUS = {
    "soll_leistung",
    "ist_leistung",
    "drehzahl_pumpe_m1",
    "verdichter_drehzahl",
}
NO_DEMAND_SHOWN_AS_DASHES = {"solltemperatur"}


@pytest.mark.parametrize("sensor", WEBIF_SENSORS, ids=lambda sensor: sensor.key)
def test_aus_reads_0_only_for_an_idle_power_or_speed(sensor):
    """Elsewhere "Aus" read as 0 too: on a rising total that is a meter reset
    to Home Assistant's statistics, which then count the next reading in full
    again; on the power limit it more likely means no limit. A text keeps
    the word."""
    if sensor.shown_unit is None:
        assert reading(sensor, "Aus") == "Aus"
    elif sensor.key in IDLE_SHOWN_AS_AUS:
        assert reading(sensor, "Aus") == 0.0
    else:
        assert reading(sensor, "Aus") is None


@pytest.mark.parametrize("sensor", WEBIF_SENSORS, ids=lambda sensor: sensor.key)
def test_no_value_reads_0_only_for_the_setpoint_temperature(sensor):
    """The page shows the setpoint as "--" while nothing is demanded; read as
    unknown it left a gap in the history, as the Modbus setpoints did.
    Elsewhere "--" is no value, not a 0."""
    expected = 0.0 if sensor.key in NO_DEMAND_SHOWN_AS_DASHES else None
    assert reading(sensor, "--") == expected


def test_an_energy_shows_the_three_decimals_the_controller_gives():
    """One decimal rounded a day's 0.013 kWh to 0.0 kWh: the finer resolution
    these sensors were taken in for."""
    energies = [
        sensor
        for sensor in WEBIF_SENSORS
        if sensor.device_class is SensorDeviceClass.ENERGY
    ]

    assert len(energies) == 12
    assert {sensor.suggested_display_precision for sensor in energies} == {3}


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


@pytest.mark.parametrize("key", ["hochdruck", "jaz_jahr", "schaltdifferenz_heizen"])
def test_a_sensor_sits_on_the_web_interface_device_with_an_id_of_its_own(key):
    """A device belongs to one config entry: the pump entry's devices are
    not the web interface entry's to add to."""
    sensor = _sensor(key, {})

    assert sensor.unique_id == f"{CONST.DEF_PREFIX}webif_{key}"
    assert sensor.device_info["identifiers"] == {(CONST.DOMAIN, DEVICES.WEBIF)}


@pytest.mark.parametrize(("key", "topic"), [("hochdruck", "WP_"), ("jaz_jahr", "ST_")])
def test_the_topic_option_names_a_sensor_like_its_modbus_siblings(key, topic):
    coordinator = SimpleNamespace(data={}, last_update_success=True)
    pump_data = {**PUMP_DATA, CONF.NAME_TOPIC_PREFIX: True}

    sensor = WebifSensor(coordinator, pump_data, _description(key))

    assert sensor.translation_placeholders == {"prefix": topic}
