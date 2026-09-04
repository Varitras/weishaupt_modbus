"""Constants for the Weishaupt Modbus API."""

DEFAULT_PORT = 502
# The controller's Modbus unit (slave) id; the only one it answers.
MODBUS_UNIT_ID = 1

# Temperature registers are signed tenths of a degree; the top of the
# negative range is reserved for conditions (Weishaupt register list).
TEMPERATURE_NO_SENSOR = 0x8000
# From here up: sensor open (0x8001), sensor short (0x8002), then further
# status words (digital off/on at 0x800A/0x800B) - none of them a temperature.
TEMPERATURE_SENSOR_OPEN = 0x8001
TEMPERATURE_RESERVED_BAND_END = 0x80FF
# The documented temperature domain, raw tenths: -50.0 to 500.0 degC. A word
# outside it is not a temperature, whatever its sign bit says.
TEMPERATURE_RAW_MIN = -500
TEMPERATURE_RAW_MAX = 5000
PERCENTAGE_NO_VALUE = 0xFFFF


# Weishaupt rates the register EEPROM for this many writes over the pump's
# lifetime. The counters below make the consumption visible; the warning
# fires once a day at the threshold, the limit (0 = none) refuses writes
# for the rest of the day.
EEPROM_WRITE_RATING = 100_000
DEFAULT_WRITE_WARNING_PER_DAY = 50
DEFAULT_WRITE_LIMIT_PER_DAY = 0
