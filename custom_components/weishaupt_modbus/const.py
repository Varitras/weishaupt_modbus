"""Constants for Weishaupt modbus integration."""

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from homeassistant.const import CONF_HOST, CONF_PORT, CONF_PREFIX


@dataclass(frozen=True)
class ConfConstants:
    """Constants used for configuration."""

    HOST: str = CONF_HOST
    PORT: str = CONF_PORT
    PREFIX: str = CONF_PREFIX
    DEVICE_POSTFIX: str = "Device-Postfix"
    KENNFELD_FILE: str = "Kennfeld-File"
    HK2: str = "Heizkreis 2"
    HK3: str = "Heizkreis 3"
    HK4: str = "Heizkreis 4"
    HK5: str = "Heizkreis 5"
    NAME_DEVICE_PREFIX: str = "Name-Device-Prefix"
    NAME_TOPIC_PREFIX: str = "Name-Topic-Prefix"


CONF = ConfConstants()


@dataclass(frozen=True)
class MainConstants:
    """Main constants."""

    DOMAIN: str = "weishaupt_modbus"
    SCAN_INTERVAL: timedelta = timedelta(seconds=30)
    # The options-flow key and the bounds the poll interval may be set to.
    # The controller answers one block read at a time; below five seconds
    # the polls overlap on a pump with every circuit enabled.
    OPTION_SCAN_INTERVAL: str = "scan_interval"
    SCAN_INTERVAL_MIN_SECONDS: int = 5
    SCAN_INTERVAL_MAX_SECONDS: int = 600
    OPTION_WRITE_WARNING_PER_DAY: str = "write_warning_per_day"
    OPTION_WRITE_LIMIT_PER_DAY: str = "write_limit_per_day"
    UNIQUE_ID: str = "unique_id"
    APPID: int = 100
    DEF_KENNFELDFILE: str = "weishaupt_wbb_kennfeld.json"
    DEF_PREFIX: str = "weishaupt_wbb"


CONST = MainConstants()


class FORMATS(StrEnum):
    """How a register's word is to be read."""

    TEMPERATURE = "temperature"
    PERCENTAGE = "percentage"
    NUMBER = "number"
    STATUS = "status"
    UNKNOWN = "unknown"
    TEXT = "text"


class TYPES(StrEnum):
    """Which entity a register becomes."""

    SENSOR = "Sensor"
    SENSOR_CALC = "Sensor_Calc"
    SELECT = "Select"
    NUMBER = "Number"
    NUMBER_RO = "Number_RO"


@dataclass(frozen=True)
class DeviceConstants:
    """Device constants."""

    SYS: str = "dev_system"
    WP: str = "dev_waermepumpe"
    WW: str = "dev_warmwasser"
    HZ: str = "dev_heizkreis"
    HZ2: str = "dev_heizkreis2"
    HZ3: str = "dev_heizkreis3"
    HZ4: str = "dev_heizkreis4"
    HZ5: str = "dev_heizkreis5"
    W2: str = "dev_waermeerzeuger2"
    ST: str = "dev_statistik"
    UK: str = "dev_unknown"
    IO: str = "dev_ein_aus"


DEVICES = DeviceConstants()
