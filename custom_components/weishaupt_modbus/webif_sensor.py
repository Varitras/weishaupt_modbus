"""The web interface's values as sensors, on a device of their own.

A value counts only in the unit the catalogue expects: a page of another
section, or a changed display, would otherwise pass for a reading.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumeFlowRate,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICES
from .entities import device_info, name_prefix
from .migrate_helpers import unique_id_from_parts
from .webif import pages
from .webif.client import Traffic
from .webif.discovery import HEAT_PUMP_PAGE, HEATING_PAGE, STATISTICS_PAGE
from .webif.setting import POWER_LIMIT_ENGLISH_TITLE, POWER_LIMIT_TITLE
from .webif_coordinator import WebifCoordinator

# The unit texts the controller shows after a number.
CELSIUS = "°C"
KELVIN = "K"
BAR = "BAR"
KILOWATT = "KW"
KILOWATT_HOURS = "KWh"
CUBIC_METRES_PER_HOUR = "m3/h"
RPM = "rpm"
HOURS = "h"
NO_UNIT = ""


@dataclass(frozen=True, kw_only=True)
class WebifSensorDescription(SensorEntityDescription):
    """A value of a web interface page: its page, its title, its unit there.

    `english_title` is the title a controller set to English shows instead,
    as it showed it on 2026-10-10: cut at 25 characters, typing errors kept.
    `shown_unit` is the text after the number; None for a value that is text.
    `topic` is the pump device whose short name the name prefix option puts
    in front, as for the Modbus sensors of the same subject.
    `off_is_zero` marks a power or speed, which the controller shows as "Aus"
    when idle; set to English, an idle power as "off". On any other number
    they read unknown, since a 0 on a rising total resets it in Home
    Assistant's statistics; a text keeps them.
    `no_value_is_zero` marks the setpoint temperature, which the controller
    shows as "--" while nothing is demanded; unknown left a gap in the
    history. Anywhere else "--" is no value.
    """

    page: str
    title: str
    english_title: str
    shown_unit: str | None
    topic: str = DEVICES.WP
    off_is_zero: bool = False
    no_value_is_zero: bool = False


def _sensor(
    key: str,
    title: str,
    english_title: str,
    shown_unit: str | None,
    page: str = HEAT_PUMP_PAGE,
    **attributes: Any,
) -> WebifSensorDescription:
    return WebifSensorDescription(
        key=key,
        translation_key=f"webif_{key}",
        page=page,
        title=title,
        english_title=english_title,
        shown_unit=shown_unit,
        **attributes,
    )


def _temperature(
    key: str, title: str, english_title: str, **attributes: Any
) -> WebifSensorDescription:
    return _sensor(
        key,
        title,
        english_title,
        CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        **attributes,
    )


def _difference(
    key: str,
    title: str,
    english_title: str,
    page: str = HEAT_PUMP_PAGE,
    **attributes: Any,
) -> WebifSensorDescription:
    return _sensor(
        key,
        title,
        english_title,
        KELVIN,
        page,
        device_class=SensorDeviceClass.TEMPERATURE_DELTA,
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        **attributes,
    )


def _pressure(key: str, title: str, english_title: str) -> WebifSensorDescription:
    return _sensor(
        key,
        title,
        english_title,
        BAR,
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    )


def _percentage(
    key: str,
    title: str,
    english_title: str,
    page: str = HEAT_PUMP_PAGE,
    **attributes: Any,
) -> WebifSensorDescription:
    return _sensor(
        key,
        title,
        english_title,
        PERCENTAGE,
        page,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        **attributes,
    )


def _power(key: str, title: str, english_title: str) -> WebifSensorDescription:
    return _sensor(
        key,
        title,
        english_title,
        KILOWATT,
        off_is_zero=True,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    )


def _count(key: str, title: str, english_title: str) -> WebifSensorDescription:
    return _sensor(
        key,
        title,
        english_title,
        NO_UNIT,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
    )


def _text(
    key: str, title: str, english_title: str, **attributes: Any
) -> WebifSensorDescription:
    return _sensor(key, title, english_title, None, **attributes)


def _energy(key: str, title: str, english_title: str) -> WebifSensorDescription:
    # Day, month and year start again from zero; a rising total reads the
    # drop as a new period, not as a loss. Three decimals, as the page shows
    # them: their finer resolution is why these values are read here at all.
    return _sensor(
        key,
        title,
        english_title,
        KILOWATT_HOURS,
        STATISTICS_PAGE,
        topic=DEVICES.ST,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
    )


def _performance(key: str, title: str, english_title: str) -> WebifSensorDescription:
    return _sensor(
        key,
        title,
        english_title,
        NO_UNIT,
        STATISTICS_PAGE,
        topic=DEVICES.ST,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    )


# Info > Wärmepumpe: the 31 values Modbus does not carry.
HEAT_PUMP_SENSORS = (
    _temperature("solltemperatur", "Solltemperatur", "setpoint", no_value_is_zero=True),
    _difference(
        "schaltdifferenz_dynamisch", "Schaltdifferenz dynamisch", "dynamic switching"
    ),
    _percentage(
        "drehzahl_pumpe_m1", "Drehzahl Pumpe M1", "pump speed", off_is_zero=True
    ),
    _sensor(
        "volumenstrom",
        "Volumenstrom",
        "volume flow",
        CUBIC_METRES_PER_HOUR,
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    _text("stellung_umschaltventil", "Stellung Umschaltventil", "change valve"),
    _power("soll_leistung", "Soll Leistung", "power set"),
    _power("ist_leistung", "Ist Leistung", "power actual"),
    _temperature(
        "expansionsventil_ag_eintritt", "Expansionsventil AG Eintr", "INLET TEMP"
    ),
    _temperature(
        "waermetauscher_ag_austritt", "Wärmetauscher AG Austrit", "OUTLET TEMP"
    ),
    _temperature("evi_sauggastemperatur", "EVI Sauggastemperatur", "EVI SUC G T"),
    _temperature("kaeltemittel_ig_austritt", "Kältemittel IG Austritt", "COOLA OUTLET"),
    _temperature("oelsumpftemperatur", "Ölsumpftemperatur", "OIL SUMP T"),
    _temperature("druckgastemperatur", "Druckgastemperatur", "PR GAS TEMP"),
    _pressure("niederdruck", "Niederdruck", "LOW PRESS"),
    _pressure("hochdruck", "Hochdruck", "HIGH PRESS"),
    _pressure("mitteldruck", "Mitteldruck", "AVER PRESS"),
    _temperature("kondensationstemperatur", "Kondensationstemperatur", "CONDENS TEMP"),
    _temperature(
        "saettigungstemperatur_evi", "Sättigungstemperatur EVI", "SATURAT TEMP"
    ),
    _difference("ueberhitzung_heizen", "Überhitzung Heizen", "EXCESS HEAT"),
    _difference("ueberhitzung_verdichter", "Überhitzung Verdichter", "EXCESS H COM"),
    _difference("ueberhitzung_evi", "Überhitzung EVI", "EXCESS H EVI"),
    _percentage("oeffnungsgrad_exv_heizen", "Öffnungsgrad EXV Heizen", "DEG OP EXV H"),
    _percentage("oeffnungsgrad_exv_kuehlen", "Öffnungsgrad EXV Kühlen", "DEGOPEXVCOOL"),
    _percentage("oeffnungsgrad_evi", "Öffnungsgrad EVI", "DEG OP EVI"),
    _sensor(
        "verdichter_drehzahl",
        "Verdichter",
        "compressor",
        RPM,
        off_is_zero=True,
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    _sensor(
        "betriebsstunden_verdichter",
        "Betriebsstd. Verdichter",
        "operating hours compresso",
        HOURS,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
    ),
    _count(
        "schaltspiele_verdichter", "Schaltspiele Verdichter", "switchings compressor"
    ),
    _count("schaltspiele_abtauen", "Schaltspiele Abtauen", "switching defrost"),
    _text(
        "version_wwp_sg",
        "Version WWP-SG",
        "Version WWP-SG",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _text(
        "version_wwp_ec_wbb",
        "Version WWP-EC WBB",
        "version WWP-EC WBB",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _text(
        "aussengeraet_variante",
        "Außengerät Variante",
        "variant",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

# Info > Statistik: day and month finer than Modbus, the year Modbus reads as 0.
STATISTICS_SENSORS = (
    _energy("th_energie_heizen_tag", "th. Energie Heizen Tag", "heat consuption day"),
    _energy("th_energie_ww_tag", "th. Energie WW Tag", "hot water consuption day"),
    _energy("th_energie_gesamt_tag", "th. Energie gesamt Tag", "consuption day"),
    _energy("el_energie_tag", "elektrische Energie Tag", "ELECTRICAL ENERGY DAY"),
    _energy(
        "th_energie_heizen_monat", "th. Energie Heizen Monat", "heat consuption month"
    ),
    _energy("th_energie_ww_monat", "th. Energie WW Monat", "hot water consuption mont"),
    _energy("th_energie_gesamt_monat", "th. Energie gesamt Monat", "consuption months"),
    _energy("el_energie_monat", "elektrische Energie Monat", "ELECTRICAL ENERGY MONTH"),
    _energy(
        "th_energie_heizen_jahr", "th. Energie Heizen Jahr", "heat consuption year"
    ),
    _energy("th_energie_ww_jahr", "th. Energie WW Jahr", "hot water consuption year"),
    _energy("th_energie_gesamt_jahr", "th. Energie gesamt Jahr", "consuption year"),
    _energy("el_energie_jahr", "elektrische Energie Jahr", "ELECTRICAL ENERGY YEAR"),
    _performance("jaz_jahr", "JAZ Jahr", "COP YEAR"),
    _performance("jaz_gesamt", "JAZ gesamt", "JAZ"),
)

# Wärmepumpe > Heizen: the switching difference, read here and not written.
HEATING_SENSORS = (
    _difference(
        "schaltdifferenz_heizen",
        "Schaltdifferenz",
        "switching difference",
        HEATING_PAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

WEBIF_SENSORS = HEAT_PUMP_SENSORS + STATISTICS_SENSORS + HEATING_SENSORS
# Beside it on the heating page, the power limit: a number (webif_number.py),
# read from the page by the same rules as the sensors.
POWER_LIMIT = _percentage(
    "leistungsbegrenzung_heizen",
    POWER_LIMIT_TITLE,
    POWER_LIMIT_ENGLISH_TITLE,
    HEATING_PAGE,
)
READ_VALUES = (*WEBIF_SENSORS, POWER_LIMIT)

# A page counts as whole only with the title of every value read from it.
REQUIRED_TITLES = {
    page: frozenset(value.title for value in READ_VALUES if value.page == page)
    for page in (HEAT_PUMP_PAGE, STATISTICS_PAGE, HEATING_PAGE)
}
# Each page's English titles, to the German ones the values are kept under.
ENGLISH_TITLES = {
    page: {
        value.english_title: value.title for value in READ_VALUES if value.page == page
    }
    for page in REQUIRED_TITLES
}

# User wish, 2026-10-03: whether the access gets worse over time shows in
# Home Assistant's statistics of these.
TRAFFIC_SENSORS = tuple(
    SensorEntityDescription(
        key=count.name,
        translation_key=f"webif_{count.name}",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    )
    for count in fields(Traffic)
)
ANSWER_TIME = SensorEntityDescription(
    key="answer_time",
    translation_key="webif_answer_time",
    device_class=SensorDeviceClass.DURATION,
    native_unit_of_measurement=UnitOfTime.SECONDS,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1,
    entity_category=EntityCategory.DIAGNOSTIC,
)


def reading(description: WebifSensorDescription, shown: str | None) -> Any:
    """The value as Home Assistant shows it; None for no value or a wrong unit."""
    if shown == pages.NO_VALUE and description.no_value_is_zero:
        return 0.0
    if shown is None or shown == pages.NO_VALUE:
        return None
    if description.shown_unit is None:
        return shown
    if shown in pages.OFF_WORDS and description.off_is_zero:
        return 0.0
    if pages.unit(shown) != description.shown_unit:
        return None
    return pages.number(shown)


def shown_in_unknown_units(page: str, found: list[tuple[str, str]]) -> frozenset[str]:
    """The titles of the page whose sensor cannot read the value shown.

    "--", "Aus" and "off" are the controller's own words for no value, not a
    unit.
    """
    sensors = {value.title: value for value in READ_VALUES if value.page == page}
    return frozenset(
        title
        for title, shown in found
        if title in sensors
        and shown != pages.NO_VALUE
        and shown not in pages.OFF_WORDS
        and reading(sensors[title], shown) is None
    )


class _WebifEntity(CoordinatorEntity[WebifCoordinator], SensorEntity):
    """A sensor on the web interface's device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WebifCoordinator,
        pump_data: Mapping[str, Any],
        description: SensorEntityDescription,
    ) -> None:
        """Place the sensor on the web interface's device."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = unique_id_from_parts(
            pump_data, f"webif_{description.key}"
        )
        # A device belongs to one config entry, so the web interface cannot
        # add to the pump entry's devices; it gets one of its own.
        self._attr_device_info = device_info(pump_data, DEVICES.WEBIF)


class WebifSensor(_WebifEntity):
    """A value of a web interface page, on the web interface's device."""

    entity_description: WebifSensorDescription

    def __init__(
        self,
        coordinator: WebifCoordinator,
        pump_data: Mapping[str, Any],
        description: WebifSensorDescription,
    ) -> None:
        """Name the sensor the way the pump's own sensors are named."""
        super().__init__(coordinator, pump_data, description)
        self._attr_translation_placeholders = {
            "prefix": name_prefix(pump_data, description.topic)
        }

    @property
    def available(self) -> bool:
        """Only while the page's last values are still shown."""
        return super().available and self._values() is not None

    @property
    def native_value(self) -> Any:
        """The shown value of the sensor's title on its page."""
        values = self._values() or {}
        return reading(
            self.entity_description, values.get(self.entity_description.title)
        )

    def _values(self) -> dict[str, str] | None:
        # No data at all until the first round, which runs after the setup.
        return (self.coordinator.data or {}).get(self.entity_description.page)


class _PollingSensor(_WebifEntity):
    """A sensor about the polling itself, beside the web interface's values."""

    @property
    def available(self) -> bool:
        """Also after the brake has stopped the polling: it explains the stop."""
        return True


class AnswerTimeSensor(_PollingSensor):
    """How long the controller took for its slowest answer since the last round."""

    @property
    def native_value(self) -> float | None:
        """The slowest answer since the last round that asked anything, a write's too."""
        return self.coordinator.answer_seconds


class TrafficSensor(_PollingSensor, RestoreSensor):
    """A count of what was asked, running on across restarts.

    The client starts its counts at zero on every (re)load; the last recorded
    state is added on top in async_added_to_hass, once: a sensor given a new
    id and its old one back is added again, and added its count twice.
    """

    _restored = False

    @property
    def native_value(self) -> int:
        """The count as the client holds it now."""
        return int(getattr(self.coordinator.traffic, self.entity_description.key))

    async def async_added_to_hass(self) -> None:
        """Add the last recorded count to the fresh client's."""
        await super().async_added_to_hass()
        if self._restored:
            return
        self._restored = True
        last_data = await self.async_get_last_sensor_data()
        if last_data is None or last_data.native_value is None:
            return
        # RestoreSensor types the value as any sensor value; ours is a count.
        recorded = int(str(last_data.native_value))
        self.coordinator.traffic.restore(self.entity_description.key, recorded)
