"""Which of the two 2nd heat source register layouts a pump has."""

import asyncio

from modbus_connection import (
    IllegalDataAddressError,
    ModbusConnectionError,
    ModbusDesyncError,
    ModbusError,
    ModbusProtocolError,
    ModbusTimeoutError,
    ServerDeviceBusyError,
)
from modbus_connection.mock import MockModbusConnection
import pytest

from custom_components.weishaupt_modbus.weishaupt_modbus_api.device import BANDS
from custom_components.weishaupt_modbus.weishaupt_modbus_api.exceptions import (
    LayoutUndetermined,
)
from custom_components.weishaupt_modbus.weishaupt_modbus_api.hpconst import (
    MODBUS_W2_ITEMS,
)
from custom_components.weishaupt_modbus.weishaupt_modbus_api.layout import (
    EP_ROW_NAMES,
    SHORT_ADDRESSES,
    W2Layout,
    detect_w2_layout,
    w2_items,
)

# What the WWP LS of upstream #167 answered: -25, -5 and -5 degC.
SHORT_PUMP = {44101: 4, 44102: 65286, 44103: 65486, 44104: 65486}


def _unit(raw=None, refused=(), errors=None):
    unit = MockModbusConnection().for_unit(1)
    unit.load_raw({"holding": raw or {}})
    for address in refused:
        unit.fail_read(address, IllegalDataAddressError(), register_type="holding")
    for address, error in (errors or {}).items():
        unit.fail_read(address, error, register_type="holding")
    return unit


async def _detect(unit):
    return await detect_w2_layout(unit, asyncio.Lock())


async def test_a_pump_answering_44105_is_long():
    assert await _detect(_unit()) is W2Layout.LONG


async def test_44105_and_44106_refused_with_44102_to_44104_answering_is_short():
    unit = _unit(SHORT_PUMP, refused=(44105, 44106))

    assert await _detect(unit) is W2Layout.SHORT


async def test_all_five_refused_is_long():
    """A pump without these registers: the poll marks them absent."""
    unit = _unit(refused=(44102, 44103, 44104, 44105, 44106))

    assert await _detect(unit) is W2Layout.LONG


async def test_a_partial_answer_is_undetermined():
    unit = _unit(SHORT_PUMP, refused=(44103, 44105, 44106))

    with pytest.raises(LayoutUndetermined):
        await _detect(unit)


async def test_44106_answering_after_44105_refused_is_undetermined():
    unit = _unit(SHORT_PUMP, refused=(44105,))

    with pytest.raises(LayoutUndetermined):
        await _detect(unit)


async def test_another_exception_code_is_undetermined():
    """Only code 2 says "not here"; a busy or failing controller says
    nothing about the layout, and a long pump read as short would write its
    temperatures into the EP configuration registers."""
    unit = _unit(SHORT_PUMP, refused=(44106,), errors={44105: ServerDeviceBusyError()})

    with pytest.raises(LayoutUndetermined):
        await _detect(unit)


@pytest.mark.parametrize(
    "error",
    [
        ModbusConnectionError("x"),
        ModbusTimeoutError("x"),
        ModbusProtocolError("x"),
        ModbusDesyncError("x"),
        ModbusError("x"),
    ],
)
async def test_a_link_error_while_detecting_propagates(error):
    unit = _unit(SHORT_PUMP, errors={44105: error})

    with pytest.raises(type(error)) as raised:
        await _detect(unit)

    assert not isinstance(raised.value, LayoutUndetermined)


async def test_detection_reads_under_the_controller_lock():
    """The controller also serves the web interface, never at once."""
    lock = asyncio.Lock()
    unit = _unit()
    await lock.acquire()

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(detect_w2_layout(unit, lock), 0.1)

    lock.release()
    assert await detect_w2_layout(unit, lock) is W2Layout.LONG


def test_the_short_rows_move_only_the_three_temperatures():
    long_rows = {row.name: row for row in MODBUS_W2_ITEMS}
    before = [(row.name, row.address) for row in MODBUS_W2_ITEMS]
    covered = {address for low, high in BANDS for address in range(low, high + 1)}

    short = w2_items(W2Layout.SHORT)

    assert not {row.name for row in short} & set(EP_ROW_NAMES)
    assert {row.name for row in short} == set(long_rows) - set(EP_ROW_NAMES)
    for row in short:
        twin = long_rows[row.name]
        assert row.address == SHORT_ADDRESSES.get(row.name, twin.address)
        assert row.address in covered
        assert (row.translation_key, row.type, row.format, row.params) == (
            twin.translation_key,
            twin.type,
            twin.format,
            twin.params,
        )
    assert SHORT_ADDRESSES == {
        "Grenztemperatur": 44102,
        "Bivalenztemperatur": 44103,
        "Bivalenztemperatur WW": 44104,
    }
    assert [(row.name, row.address) for row in MODBUS_W2_ITEMS] == before


def test_the_long_rows_are_the_table_rows():
    assert w2_items(W2Layout.LONG) is MODBUS_W2_ITEMS
