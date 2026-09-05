"""The heat pump over a modbus-connection unit, built from the register table.

The controller serves its registers in bands (see BANDS). A block read that
crosses a band end is answered with a broken exception frame, so every band
is its own component and one block read; a band the pump does not have
(a second heat source on a model without one) answers an exception and is
simply marked absent. The unit itself - the socket, the lock, reconnects -
belongs to Home Assistant's modbus integration.
"""

import asyncio
from collections import defaultdict
import logging
from typing import Any

from modbus_connection import ModbusExceptionError, ModbusUnit
from modbus_connection.model import Component

from .const import EEPROM_WRITE_RATING, SETPOINT_OFF_SIGNED
from .exceptions import SystemBandRefused, WriteError
from .fields import NO_SENSOR, OFF, field_for
from .hpconst import TYPES, ModbusItem
from .write_budget import WriteBudget

_LOGGER = logging.getLogger(__name__)

Band = tuple[int, int]

# The contiguous address ranges the controller serves, from the Weishaupt
# register lists and a scan of a WBB (2026-07). Heating circuits 2-5 sit
# 100, 200, 300, 400 above circuit 1. A read must never cross a band end.
BANDS: tuple[Band, ...] = (
    (30001, 30006),
    (40001, 40002),
    *((31101 + 100 * circuit, 31106 + 100 * circuit) for circuit in range(5)),
    *((41101 + 100 * circuit, 41112 + 100 * circuit) for circuit in range(5)),
    (32101, 32102),
    (42101, 42105),
    (33101, 33111),
    (33126, 33126),
    (43101, 43110),
    (34101, 34107),
    (44101, 44103),
    (44104, 44106),
    (35101, 35108),
    (45101, 45108),
    *((36001 + 100 * group, 36004 + 100 * group) for group in range(1, 8)),
    (36801, 36801),
)
HOLDING_REGISTERS = range(40000, 50000)
# Outside temperature to operating status: every Weishaupt serves these.
SYSTEM_BAND: Band = (30001, 30006)


def band_of(address: int) -> Band:
    """The band an address lies in.

    Raises ValueError for an address outside every band: a table row that
    would be read from a block the controller refuses.
    """
    for band in BANDS:
        if band[0] <= address <= band[1]:
            return band
    raise ValueError(f"register {address} lies in no known band")


def _apply(row: ModbusItem, value: Any) -> None:
    """What one decoded word means for the row: absent, off, or a value."""
    row.is_invalid = value is NO_SENSOR
    row.is_off = value is OFF
    row.state = None if row.is_invalid or row.is_off else value
    if row.state is not None:
        row.last_setting = row.state


def _field_name(item: ModbusItem) -> str:
    return f"register_{item.address}"


class WeishauptHeatPump:
    """Reads the table's registers in one block per band; writes one at a time."""

    def __init__(
        self, unit: ModbusUnit, items: list[ModbusItem], write_budget: WriteBudget
    ) -> None:
        """Group the register rows by band and build a component per band."""
        self.items = [item for item in items if item.type != TYPES.SENSOR_CALC]
        self.write_budget = write_budget
        # The budget decision and the write it guards are one operation: two
        # automations firing together both saw one allowance left and both
        # wrote. The backend's own lock serialises the wire, not this.
        self._write_lock = asyncio.Lock()
        self.present: dict[Band, bool] = {}
        self._components: dict[Band, Component] = {}
        self._rows: dict[Band, list[ModbusItem]] = defaultdict(list)
        for item in self.items:
            self._rows[band_of(item.address)].append(item)
        for band, rows in self._rows.items():
            space = "holding" if band[0] in HOLDING_REGISTERS else "input"
            component_class = type(
                f"Band{band[0]}",
                (Component,),
                {
                    "register_space": space,
                    **{_field_name(row): field_for(row) for row in rows},
                },
            )
            self._components[band] = component_class(unit)
            self.present[band] = True

    async def async_update(self) -> None:
        """Read every band; a band the pump refuses is absent, not an error.

        A link problem (ModbusConnectionError, ModbusTimeoutError) propagates:
        that is the coordinator's failed refresh, not an absent module. So
        does a refused system band: a wrong device under the address answered
        every block with an exception and counted as a healthy poll.
        """
        # Read everything first, apply afterwards: the rows are what the
        # entities show, and a poll that fails halfway used to leave them
        # half new, half old - published as if the poll had succeeded.
        served: dict[Band, Component | None] = {}
        for band, component in self._components.items():
            try:
                await component.async_update()
            except ModbusExceptionError as err:
                # Any code: the controller answers a refused block with a
                # malformed exception frame whose code is not meaningful.
                if self.present[band]:
                    _LOGGER.debug("Registers %d-%d not served: %s", *band, err)
                served[band] = None
                continue
            served[band] = component
        if SYSTEM_BAND in served and served[SYSTEM_BAND] is None:
            raise SystemBandRefused(
                f"registers {SYSTEM_BAND[0]}-{SYSTEM_BAND[1]} refused; "
                "is this a Weishaupt controller?"
            )
        for band, answered in served.items():
            if answered is None:
                self._mark_absent(band)
                continue
            self.present[band] = True
            for row in self._rows[band]:
                _apply(row, getattr(answered, _field_name(row)))

    def _mark_absent(self, band: Band) -> None:
        self.present[band] = False
        for row in self._rows[band]:
            row.state = None
            row.is_invalid = True
            row.is_off = False

    async def write(self, item: ModbusItem, value: int) -> bool:
        """Write a raw register word; False when it was already active.

        The EEPROM is rated for EEPROM_WRITE_RATING writes, so an unchanged
        value is not written again and the daily limit is honoured.
        """
        async with self._write_lock:
            if item.state is not None and item.state == value:
                _LOGGER.debug(
                    "Register %d already holds %d, not written", item.address, value
                )
                return False
            await self._write_word(item, value)
            item.state = value
            item.is_off = False
            item.last_setting = value
            return True

    async def write_off(self, item: ModbusItem) -> bool:
        """Switch a setpoint off (its off word); False when it already was."""
        async with self._write_lock:
            if item.is_off:
                return False
            await self._write_word(item, SETPOINT_OFF_SIGNED)
            item.state = None
            item.is_off = True
            return True

    async def _write_word(self, item: ModbusItem, word: int) -> None:
        if not self.write_budget.allows_write():
            raise WriteError(
                f"Daily write limit of {self.write_budget.limit} reached; "
                f"register {item.address} not written"
            )
        component = self._components[band_of(item.address)]
        await component.write(_field_name(item), word)
        if self.write_budget.record_write():
            _LOGGER.warning(
                "%d register writes today. The EEPROM is rated for %d writes "
                "in total; check the automations that set values",
                self.write_budget.writes_today,
                EEPROM_WRITE_RATING,
            )

    def value_of(self, address: int) -> Any:
        """The last value read for a register, None when absent or unread."""
        return next((row.state for row in self.items if row.address == address), None)
