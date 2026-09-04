"""The register-table row as a modbus-connection field.

Every field hands out the RAW register word the way the client always did
(tenths of a degree, a status number, a count); the entities scale and
translate. What the field adds is the sentinel handling: an absent sensor
becomes NO_SENSOR, a fault or status word becomes None.
"""

from typing import Any

from modbus_connection.model.fields import NumberField

from .const import (
    PERCENTAGE_NO_VALUE,
    TEMPERATURE_NO_SENSOR,
    TEMPERATURE_RAW_MAX,
    TEMPERATURE_RAW_MIN,
    TEMPERATURE_RESERVED_BAND_END,
    TEMPERATURE_SENSOR_OPEN,
)
from .hpconst import FORMATS, TYPES, ModbusItem


class NoSensor:
    """The register answered "nothing is connected here"."""

    def __repr__(self) -> str:
        """Read well in a test failure."""
        return "NO_SENSOR"


NO_SENSOR = NoSensor()


class RegisterWord(NumberField[int]):
    """A 16-bit register word with the controller's sentinels.

    ``absent`` is the word that means "no sensor" (the entity goes
    unavailable); ``no_reading`` is the closed range of words that mean "a
    sensor, but no usable value" (the entity stays, with no state);
    ``plausible`` is the closed range a decoded value may lie in - outside
    it the word is a status or garbage, not a reading.
    """

    def __init__(
        self,
        address: int,
        *,
        signed: bool,
        writable: bool,
        absent: int | None = None,
        no_reading: tuple[int, int] | None = None,
        plausible: tuple[int, int] | None = None,
    ) -> None:
        """A word at ``address``; ``absent`` and ``no_reading`` are its sentinels."""
        super().__init__(address, signed=signed, writable=writable)
        self.absent = absent
        self.no_reading = no_reading
        self.plausible = plausible

    def decode(self, words: list[int], scale_exponent: int | None = None) -> Any:
        """NO_SENSOR, None or the signed/unsigned word."""
        raw = words[0]
        if raw == self.absent:
            return NO_SENSOR
        if (
            self.no_reading is not None
            and self.no_reading[0] <= raw <= self.no_reading[1]
        ):
            return None
        value = super().decode(words, scale_exponent)
        if self.plausible is not None and not (
            self.plausible[0] <= value <= self.plausible[1]
        ):
            return None
        return value


def field_for(item: ModbusItem) -> RegisterWord:
    """The field that reads (and, for a setting, writes) this table row."""
    writable = item.type in (TYPES.NUMBER, TYPES.SELECT)
    if item.format == FORMATS.TEMPERATURE:
        return RegisterWord(
            item.address,
            signed=True,
            writable=writable,
            absent=TEMPERATURE_NO_SENSOR,
            no_reading=(TEMPERATURE_SENSOR_OPEN, TEMPERATURE_RESERVED_BAND_END),
            plausible=(TEMPERATURE_RAW_MIN, TEMPERATURE_RAW_MAX),
        )
    if item.format == FORMATS.PERCENTAGE:
        return RegisterWord(
            item.address, signed=False, writable=writable, absent=PERCENTAGE_NO_VALUE
        )
    return RegisterWord(item.address, signed=False, writable=writable)
