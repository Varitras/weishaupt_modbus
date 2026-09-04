"""The heat pump over modbus-connection: one block per band, sentinels, absence, writes.

Driven against the library's in-memory mock unit - the same code path the
tmodbus backend takes, minus the wire.
"""

import copy
import itertools
import json
import pathlib

from modbus_connection import (
    IllegalDataAddressError,
    ModbusConnectionError,
    ModbusExceptionError,
)
from modbus_connection.mock import MockModbusConnection
import pytest

from custom_components.weishaupt_modbus.const import DEVICES, FORMATS, TYPES
from custom_components.weishaupt_modbus.items import ModbusItem
from custom_components.weishaupt_modbus.weishaupt_modbus_api import hpconst
from custom_components.weishaupt_modbus.weishaupt_modbus_api.device import (
    BANDS,
    HOLDING_REGISTERS,
    WeishauptHeatPump,
    band_of,
)
from custom_components.weishaupt_modbus.weishaupt_modbus_api.exceptions import (
    WriteError,
)
from custom_components.weishaupt_modbus.weishaupt_modbus_api.write_budget import (
    WriteBudget,
)

OUTSIDE_TEMPERATURE = 30001
CONSTANT_LOWERING = 41111  # TEMPERATURE setpoint whose 0x8000 means off
POWER_REQUEST = 33103  # PERCENTAGE
PV_SETPOINT = 40002  # NUMBER, holding
BIVALENCE_TEMPERATURE = 44105  # TEMPERATURE, holding, writable
SECOND_HEAT_SOURCE_STATUS = 34101


def _all_items() -> list[ModbusItem]:
    return [copy.deepcopy(item) for group in hpconst.DEVICELISTS for item in group]


@pytest.fixture
def unit():
    return MockModbusConnection().for_unit(1)


@pytest.fixture
def pump(unit):
    return WeishauptHeatPump(unit, _all_items(), WriteBudget(warn_at=0, limit=0))


def _documented_registers() -> set[int]:
    """Every address the manufacturer documents, from the checked-in list.

    Read from a file rather than derived from BANDS: a guard whose oracle
    is the thing it guards passes whatever BANDS happens to say (audit
    2026-09-03). Changing a band now means changing this file too, and
    that means going back to the data-point list.
    """
    path = pathlib.Path(__file__).with_name("documented_registers.json")
    return set(json.loads(path.read_text(encoding="utf-8"))["addresses"])


def _row(pump, address):
    return next(item for item in pump.items if item.address == address)


# --- the bands ------------------------------------------------------------


def test_the_bands_do_not_overlap():
    for (_, high), (low, _) in itertools.pairwise(sorted(BANDS)):
        assert high < low


def test_every_table_register_lies_in_a_band():
    """A row outside every band would be read from a block the controller
    refuses - and take its whole band down with it."""
    for item in _all_items():
        if item.type != TYPES.SENSOR_CALC:
            band_of(item.address)


def test_the_bands_cover_exactly_the_documented_registers():
    """A band reaching past the last documented register makes the block
    read cross into addresses the controller does not serve, and the whole
    band answers a broken exception frame; a band that stops short leaves
    documented rows unreadable."""
    covered = {address for low, high in BANDS for address in range(low, high + 1)}

    assert covered == _documented_registers()


def test_no_band_mixes_input_and_holding_registers():
    """One band is one block read, and the two spaces take different
    function codes (FC4 and FC3)."""
    for low, high in BANDS:
        assert (low in HOLDING_REGISTERS) == (high in HOLDING_REGISTERS), (
            f"band {low}-{high} spans both register spaces"
        )


def test_an_address_outside_every_band_is_refused():
    with pytest.raises(ValueError, match="no known band"):
        band_of(39901)


async def test_an_update_reads_each_band_in_one_block_and_never_past_its_end(
    pump, unit
):
    """Live (2026-09-02): a block that crosses a band end is answered with a
    broken exception frame; a block that stays inside may be as wide as the
    band. So one read per band, none wider."""
    await pump.async_update()

    reads = unit.read_events
    assert len(reads) == len({band_of(item.address) for item in pump.items})
    for read in reads:
        low, high = band_of(read.address)
        assert read.address >= low
        assert read.address + read.count - 1 <= high, f"{read} crosses the band end"


async def test_every_register_row_gets_its_value(pump, unit):
    unit.load_raw(
        {
            "input": {OUTSIDE_TEMPERATURE: 123, POWER_REQUEST: 42},
            "holding": {PV_SETPOINT: 7},
        }
    )

    await pump.async_update()

    assert _row(pump, OUTSIDE_TEMPERATURE).state == 123
    assert _row(pump, POWER_REQUEST).state == 42
    assert _row(pump, PV_SETPOINT).state == 7
    assert all(not item.is_invalid for item in pump.items)


# --- sentinels ------------------------------------------------------------


async def test_no_sensor_reads_as_none_and_marks_the_row_absent(pump, unit):
    unit.load_raw({"input": {OUTSIDE_TEMPERATURE: 0x8000, POWER_REQUEST: 0xFFFF}})

    await pump.async_update()

    for address in (OUTSIDE_TEMPERATURE, POWER_REQUEST):
        assert _row(pump, address).state is None
        assert _row(pump, address).is_invalid is True


@pytest.mark.parametrize("word", [0x8100, 0x7FFF, 0xFDA7])
async def test_a_temperature_word_outside_the_documented_domain_is_no_reading(
    pump, unit, word
):
    """The register list documents -50.0 to 500.0 degC. 0x8100 decoded as
    -3251.2 degC and 0x7FFF as 3276.7 degC - published as measurements, and
    recorded. 0xFDA7 is -50.1 degC, one tenth below the domain."""
    unit.load_raw({"input": {OUTSIDE_TEMPERATURE: word}})

    await pump.async_update()

    assert _row(pump, OUTSIDE_TEMPERATURE).state is None
    assert _row(pump, OUTSIDE_TEMPERATURE).is_invalid is False


async def test_a_setpoint_at_its_off_word_is_off_not_absent(pump, unit):
    """Live: 41111 and 42105 read 0x8000, and the controller's menu shows
    "Aus" for them. Treated as a missing sensor, the entity went unavailable
    and the setting could not be turned back on from Home Assistant."""
    unit.load_raw({"holding": {CONSTANT_LOWERING: 0x8000}})

    await pump.async_update()

    row = _row(pump, CONSTANT_LOWERING)
    assert row.is_off is True
    assert row.is_invalid is False
    assert row.state is None


async def test_a_sensor_at_the_same_word_is_still_absent(pump, unit):
    unit.load_raw({"input": {OUTSIDE_TEMPERATURE: 0x8000}})

    await pump.async_update()

    assert _row(pump, OUTSIDE_TEMPERATURE).is_invalid is True
    assert _row(pump, OUTSIDE_TEMPERATURE).is_off is False


async def test_switching_a_setpoint_off_writes_the_off_word(pump, unit):
    writes = []
    unit.on_write(writes.append)
    unit.load_raw({"holding": {CONSTANT_LOWERING: 185}})
    await pump.async_update()
    row = _row(pump, CONSTANT_LOWERING)

    assert await pump.write_off(row) is True

    assert writes[0].values == [0x8000]
    assert row.is_off is True
    assert row.state is None
    assert row.last_setting == 185, "the value to come back to"
    assert await pump.write_off(row) is False, "already off: no EEPROM write"


async def test_writing_a_value_switches_the_setpoint_back_on(pump, unit):
    unit.load_raw({"holding": {CONSTANT_LOWERING: 0x8000}})
    await pump.async_update()
    row = _row(pump, CONSTANT_LOWERING)

    await pump.write(row, 185)

    assert row.is_off is False
    assert row.state == 185


async def test_the_coldest_documented_temperature_is_still_a_reading(pump, unit):
    unit.load_raw({"input": {OUTSIDE_TEMPERATURE: 0xFE0C}})  # -500 = -50.0 degC

    await pump.async_update()

    assert _row(pump, OUTSIDE_TEMPERATURE).state == -500


@pytest.mark.parametrize("word", [0x8001, 0x8002, 0x800A, 0x80FF])
async def test_a_faulty_sensor_or_status_word_is_no_reading_but_present(
    pump, unit, word
):
    unit.load_raw({"input": {OUTSIDE_TEMPERATURE: word}})

    await pump.async_update()

    assert _row(pump, OUTSIDE_TEMPERATURE).state is None
    assert _row(pump, OUTSIDE_TEMPERATURE).is_invalid is False


async def test_a_negative_temperature_is_signed(pump, unit):
    unit.load_raw({"input": {OUTSIDE_TEMPERATURE: 65436}})

    await pump.async_update()

    assert _row(pump, OUTSIDE_TEMPERATURE).state == -100


async def test_a_status_number_is_passed_through(pump, unit):
    unit.load_raw({"input": {30006: 25}})

    await pump.async_update()

    assert _row(pump, 30006).state == 25


# --- absence ----------------------------------------------------------------


async def test_a_refused_band_is_absent_and_the_others_still_read(pump, unit):
    """A pump without a second heat source refuses 34101-34107; the WBB the
    table was built on serves them. Absent, not broken - and only that band."""
    unit.load_raw({"input": {OUTSIDE_TEMPERATURE: 123}})
    unit.fail_read(
        SECOND_HEAT_SOURCE_STATUS, IllegalDataAddressError(), register_type="input"
    )

    await pump.async_update()

    assert pump.present[band_of(SECOND_HEAT_SOURCE_STATUS)] is False
    assert _row(pump, SECOND_HEAT_SOURCE_STATUS).state is None
    assert _row(pump, SECOND_HEAT_SOURCE_STATUS).is_invalid is True
    assert _row(pump, OUTSIDE_TEMPERATURE).state == 123


async def test_any_exception_code_means_absent(pump, unit):
    """The controller answers a refused block with a code that is not a
    real exception code (2 x count) - the class of the error is what counts."""
    unit.fail_read(
        SECOND_HEAT_SOURCE_STATUS, ModbusExceptionError(0x0E), register_type="input"
    )

    await pump.async_update()

    assert pump.present[band_of(SECOND_HEAT_SOURCE_STATUS)] is False


async def test_a_band_that_answers_again_is_present_again(pump, unit):
    unit.fail_read(
        SECOND_HEAT_SOURCE_STATUS, IllegalDataAddressError(), register_type="input"
    )
    await pump.async_update()
    unit.fail_read(SECOND_HEAT_SOURCE_STATUS, None, register_type="input")
    unit.load_raw({"input": {SECOND_HEAT_SOURCE_STATUS: 3}})

    await pump.async_update()

    assert pump.present[band_of(SECOND_HEAT_SOURCE_STATUS)] is True
    assert _row(pump, SECOND_HEAT_SOURCE_STATUS).state == 3


async def test_a_dead_link_is_raised_not_swallowed(pump, unit):
    unit.fail_requests(ModbusConnectionError("link down"))

    with pytest.raises(ModbusConnectionError):
        await pump.async_update()


# --- writes -------------------------------------------------------------------


async def test_a_changed_value_is_written_and_remembered(pump, unit):
    writes = []
    unit.on_write(writes.append)
    row = _row(pump, PV_SETPOINT)

    assert await pump.write(row, 42) is True

    assert [(event.address, event.values) for event in writes] == [(PV_SETPOINT, [42])]
    assert row.state == 42
    assert pump.write_budget.total == 1


async def test_an_unchanged_value_is_not_written_again(pump, unit):
    writes = []
    unit.on_write(writes.append)
    row = _row(pump, PV_SETPOINT)
    row.state = 42

    assert await pump.write(row, 42) is False

    assert writes == []
    assert pump.write_budget.total == 0


async def test_a_negative_temperature_is_written_as_twos_complement(pump, unit):
    writes = []
    unit.on_write(writes.append)

    await pump.write(_row(pump, BIVALENCE_TEMPERATURE), -50)

    assert writes[0].values == [65486]


async def test_the_daily_limit_refuses_the_write(pump, unit):
    pump.write_budget.limit = 1
    row = _row(pump, PV_SETPOINT)
    await pump.write(row, 1)

    with pytest.raises(WriteError, match="limit"):
        await pump.write(row, 2)

    assert row.state == 1


async def test_a_read_only_register_cannot_be_written(pump, unit):
    with pytest.raises(AttributeError):
        await pump.write(_row(pump, OUTSIDE_TEMPERATURE), 1)


async def test_reaching_the_warning_threshold_is_logged_once(pump, unit, caplog):
    pump.write_budget.warn_at = 2
    row = _row(pump, PV_SETPOINT)

    for value in (1, 2, 3):
        await pump.write(row, value)

    assert caplog.text.count("register writes today") == 1


def test_a_calculated_sensor_is_not_a_register(unit):
    calc = ModbusItem(
        33103, "calc", FORMATS.NUMBER, TYPES.SENSOR_CALC, DEVICES.WP, "calc"
    )
    pump = WeishauptHeatPump(unit, [calc], WriteBudget(warn_at=0, limit=0))

    assert pump.items == []


H1_2_INPUT = 35103  # temperature input, or a digital status word


async def test_a_switch_input_reads_as_no_temperature(pump, unit):
    """Live: 35103 answers 0x800A (digital OFF) on a pump that uses H1.2 as a
    switch input; read as a temperature that was -3275.8 °C."""
    unit.load_raw({"input": {H1_2_INPUT: 0x800A}})

    await pump.async_update()

    assert _row(pump, H1_2_INPUT).state is None
    assert _row(pump, H1_2_INPUT).is_invalid is False


async def test_a_temperature_input_reads_in_tenths(pump, unit):
    unit.load_raw({"input": {H1_2_INPUT: 215}})

    await pump.async_update()

    assert _row(pump, H1_2_INPUT).state == 215
