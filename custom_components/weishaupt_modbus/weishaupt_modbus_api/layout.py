"""Where a pump keeps the temperatures of its 2nd heat source.

Two layouts are known. The WBB (Biblock) and the Evoblock hold the EP1/EP2
configuration at 44102/44103 and limit, bivalence and DHW bivalence
temperature at 44104-44106. The WWP LS, like the manufacturer's 2022 list,
has no EP1/EP2 rows: the three temperatures sit at 44102-44104 and 44105/44106
are refused. Neither firmware nor model can be read over Modbus, so the setup
asks the registers themselves, one at a time: a refused 3-register block
answers code 6, which the library retries as "busy" until it times out.
"""

import asyncio
import copy
from enum import Enum

from modbus_connection import IllegalDataAddressError, ModbusExceptionError, ModbusUnit

from custom_components.weishaupt_modbus.items import ModbusItem

from .device import read_word
from .exceptions import LayoutUndetermined
from .hpconst import MODBUS_W2_ITEMS

# The short layout's addresses of the rows that move; the names are the rows'
# unique-id keys, so each keeps its entity and history.
SHORT_ADDRESSES: dict[str, int] = {
    "Grenztemperatur": 44102,
    "Bivalenztemperatur": 44103,
    "Bivalenztemperatur WW": 44104,
}
# Rows only the long layout has.
EP_ROW_NAMES = ("Konfiguration EP1", "Konfiguration EP2")
# The bivalence temperatures of the long layout: answered, the pump is long.
LONG_ONLY = (44105, 44106)


class W2Layout(Enum):
    """The two register layouts of the 2nd heat source."""

    LONG = "long"
    SHORT = "short"


async def detect_w2_layout(unit: ModbusUnit, host_lock: asyncio.Lock) -> W2Layout:
    """The pump's layout, from single-register reads.

    Short only when 44105 and 44106 are refused and 44102-44104 all answer:
    a long pump read as short would write its temperatures into the EP1/EP2
    configuration. Anything between the two raises LayoutUndetermined; link
    errors propagate.
    """

    async def refused(address: int) -> bool:
        try:
            await read_word(unit, host_lock, address)
        except IllegalDataAddressError:
            return True
        except ModbusExceptionError as err:
            raise LayoutUndetermined(f"register {address}: {err!r}") from err
        return False

    if not await refused(LONG_ONLY[0]):
        return W2Layout.LONG
    if not await refused(LONG_ONLY[1]):
        raise LayoutUndetermined("44105 refused, 44106 answered")
    answers = {not await refused(address) for address in SHORT_ADDRESSES.values()}
    if answers == {True}:
        return W2Layout.SHORT
    if answers == {False}:
        # Neither layout's temperatures: the poll marks the registers absent.
        return W2Layout.LONG
    raise LayoutUndetermined("44102-44104 answered only in part")


def w2_items(layout: W2Layout) -> list[ModbusItem]:
    """The 2nd heat source rows of the layout; the long ones are the table's."""
    if layout is W2Layout.LONG:
        return MODBUS_W2_ITEMS
    rows = []
    for item in MODBUS_W2_ITEMS:
        if item.name in EP_ROW_NAMES:
            continue
        row = copy.deepcopy(item)
        row.address = SHORT_ADDRESSES.get(item.name, item.address)
        rows.append(row)
    return rows
