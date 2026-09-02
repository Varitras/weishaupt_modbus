"""The batch-reading Modbus client: what the wire says and what the cache holds.

Every test drives WeishauptModbusClient against a fake pymodbus client, so the
sentinel handling, the block batching, the write protections and the reconnect
backoff are exercised as the coordinator sees them - through `update()`,
`write_register()` and `connect()`, never by calling the helpers directly.
"""

import asyncio

import pytest

from custom_components.weishaupt_modbus.items import ModbusItem
from custom_components.weishaupt_modbus.weishaupt_modbus_api.const import (
    BACKOFF_THRESHOLD_FAILURES,
    MAX_BLOCK_READ_COUNT,
)
from custom_components.weishaupt_modbus.weishaupt_modbus_api.exceptions import (
    ConnectionFailedError,
    WriteError,
)
from custom_components.weishaupt_modbus.weishaupt_modbus_api.modbus_api import (
    WeishauptModbusClient,
)

# Registers of known format, taken from the register table the client polls.
OUTSIDE_TEMPERATURE = 30001  # TEMPERATURE, input register
POWER_REQUEST = 33103  # PERCENTAGE, input register
OPERATING_STATE = 30006  # STATUS, input register
SYSTEM_MODE = 40001  # STATUS, holding register (SELECT)
PV_SETPOINT = 40002  # NUMBER, holding register

NO_SENSOR = 32768
NO_VALUE = 65535
ILLEGAL_DATA_ADDRESS = 2


class FakeResponse:
    def __init__(self, registers=None, exception_code=None):
        self.registers = registers or []
        self.exception_code = exception_code

    def isError(self):
        return self.exception_code is not None


class FakePymodbus:
    """Enough of AsyncModbusTcpClient to answer block reads and writes."""

    def __init__(self):
        self.connected = False
        self.refuse_connections = False
        self.connect_calls = 0
        self.registers: dict = {}
        self.error_blocks: dict = {}
        self.reads: list = []
        self.writes: list = []
        self.write_error = False

    async def connect(self):
        self.connect_calls += 1
        self.connected = not self.refuse_connections

    def close(self):
        self.connected = False

    def _block(self, address, count):
        self.reads.append((address, count))
        if address in self.error_blocks:
            return FakeResponse(exception_code=self.error_blocks[address])
        return FakeResponse(
            registers=[self.registers.get(address + i, 0) for i in range(count)]
        )

    async def read_input_registers(self, address, count, device_id):
        return self._block(address, count)

    async def read_holding_registers(self, address, count, device_id):
        return self._block(address, count)

    async def write_register(self, address, value, device_id):
        self.writes.append((address, value))
        if self.write_error:
            return FakeResponse(exception_code=4)
        return FakeResponse(registers=[value])


@pytest.fixture
def wire():
    return FakePymodbus()


@pytest.fixture
async def client(wire):
    # pymodbus binds its transport to the running loop at construction, so the
    # client can only be built inside one.
    client = WeishauptModbusClient(host="127.0.0.1")
    client._client = wire
    wire.connected = True
    # The address->item map is built by the first update; the tests read it
    # before that to set up sentinels, so build it here.
    client._process_and_validate_batches(client._items)
    return client


def _item(client, address):
    return client._items_dict[address]


# --- sentinels ------------------------------------------------------------


async def test_no_sensor_sentinel_reads_as_none_and_marks_the_item_invalid(
    client, wire
):
    wire.registers[OUTSIDE_TEMPERATURE] = NO_SENSOR

    data = await client.update()

    assert data[OUTSIDE_TEMPERATURE] is None
    assert _item(client, OUTSIDE_TEMPERATURE).is_invalid is True


async def test_a_negative_temperature_is_read_as_twos_complement(client, wire):
    wire.registers[OUTSIDE_TEMPERATURE] = 65436

    data = await client.update()

    assert data[OUTSIDE_TEMPERATURE] == -100
    assert _item(client, OUTSIDE_TEMPERATURE).is_invalid is False


async def test_a_plain_temperature_is_kept_and_clears_the_invalid_flag(client, wire):
    wire.registers[OUTSIDE_TEMPERATURE] = 250
    _item(client, OUTSIDE_TEMPERATURE).is_invalid = True

    data = await client.update()

    assert data[OUTSIDE_TEMPERATURE] == 250
    assert _item(client, OUTSIDE_TEMPERATURE).is_invalid is False


async def test_percentage_sentinel_reads_as_none(client, wire):
    wire.registers[POWER_REQUEST] = NO_VALUE

    data = await client.update()

    assert data[POWER_REQUEST] is None
    assert _item(client, POWER_REQUEST).is_invalid is True


async def test_a_status_value_is_passed_through_untouched(client, wire):
    wire.registers[OPERATING_STATE] = 19

    data = await client.update()

    assert data[OPERATING_STATE] == 19
    assert _item(client, OPERATING_STATE).is_invalid is False


async def test_a_calculated_sensor_sharing_an_address_does_not_shadow_the_register(
    client, wire
):
    """33103 is the power request (PERCENTAGE) and, at the same address, the
    calculated heating power. Keyed by address the calculated item won, and
    the percentage sentinel was handed to the entity as 65535 %."""
    wire.registers[POWER_REQUEST] = NO_VALUE

    data = await client.update()

    assert data[POWER_REQUEST] is None
    assert _item(client, POWER_REQUEST).type != "Sensor_Calc"


# --- block reads ----------------------------------------------------------


async def test_illegal_address_block_nullifies_every_register_in_it(client, wire):
    """Exception 2 answers the whole block: a hardware module that is not
    installed. Every register of that block is absent, not just the first."""
    wire.registers[OUTSIDE_TEMPERATURE] = 250
    wire.error_blocks[OUTSIDE_TEMPERATURE] = ILLEGAL_DATA_ADDRESS

    data = await client.update()

    start, count = next(read for read in wire.reads if read[0] == OUTSIDE_TEMPERATURE)
    assert count > 1, "the block has to hold more than one register to prove this"
    for address in range(start, start + count):
        assert data[address] is None
        assert _item(client, address).is_invalid is True


async def test_holding_registers_are_read_with_the_holding_call(client, wire):
    calls = []

    async def holding(address, count, device_id):
        calls.append(address)
        return wire._block(address, count)

    wire.read_holding_registers = holding
    await client.update()

    assert SYSTEM_MODE in calls
    assert OUTSIDE_TEMPERATURE not in calls


def test_batch_size_never_exceeds_the_hardware_limit(client):
    """The pump answers at most five registers per block read.

    Proven on a table that VIOLATES the limit, not on the shipped one: the
    shipped batch numbers already group by fives, so the guard in the code was
    never exercised by it - the mutation run showed the check could be deleted
    without a test noticing.
    """
    seven_in_one_batch = [
        ModbusItem(
            30001 + offset,
            f"r{offset}",
            "number",
            "Sensor",
            "dev_system",
            f"k{offset}",
            batch=30001,
        )
        for offset in range(7)
    ]

    batches = client._process_and_validate_batches(seven_in_one_batch)

    assert max(batches.values()) <= MAX_BLOCK_READ_COUNT
    assert sum(batches.values()) == len(seven_in_one_batch), "a register was dropped"
    assert batches[30001] == MAX_BLOCK_READ_COUNT


def test_the_shipped_table_reads_blocks_in_address_order(client):
    batches = client._process_and_validate_batches(client._items)

    assert batches, "no batches at all - the scan proves nothing"
    assert max(batches.values()) <= MAX_BLOCK_READ_COUNT
    assert list(batches) == sorted(batches), "blocks are read in address order"


def test_every_batched_register_is_read_once(client):
    """The batches cover every non-calculated item exactly once."""
    batches = client._process_and_validate_batches(client._items)

    covered = sum(batches.values())
    polled = [
        item
        for item in client._items_dict.values()
        if item.type != "Sensor_Calc" and item.batch is not None
    ]
    assert covered == len(polled)


# --- connection -----------------------------------------------------------


async def test_update_without_connection_raises_when_the_reconnect_fails(client, wire):
    wire.connected = False
    wire.refuse_connections = True

    with pytest.raises(ConnectionFailedError):
        await client.update()


async def test_update_reconnects_first_when_the_link_is_down(client, wire):
    wire.connected = False
    wire.registers[OUTSIDE_TEMPERATURE] = 250

    data = await client.update()

    assert wire.connect_calls == 1
    assert data[OUTSIDE_TEMPERATURE] == 250


async def test_backoff_window_skips_the_connect_attempt(client, wire):
    """After the threshold, a further attempt inside the window returns
    without touching the wire - a pump that is off must not be hammered."""
    wire.connected = False
    wire.refuse_connections = True
    for _ in range(BACKOFF_THRESHOLD_FAILURES):
        assert await client.connect() is False
    attempts_before = wire.connect_calls

    assert await client.connect() is False

    assert wire.connect_calls == attempts_before, "the window was not honoured"


async def test_a_startup_connect_ignores_the_backoff_window(client, wire):
    wire.connected = False
    wire.refuse_connections = True
    for _ in range(BACKOFF_THRESHOLD_FAILURES):
        await client.connect()
    attempts_before = wire.connect_calls

    await client.connect(startup=True)

    assert wire.connect_calls == attempts_before + 1


async def test_a_successful_connect_resets_the_failure_count(client, wire):
    wire.connected = False
    wire.refuse_connections = True
    for _ in range(BACKOFF_THRESHOLD_FAILURES - 1):
        await client.connect()
    wire.refuse_connections = False

    assert await client.connect() is True
    assert client._failed_reconnect_counter == 0


async def test_a_connect_already_pending_is_not_started_twice(client, wire):
    wire.connected = False
    client._connect_pending = True

    await client.connect()

    assert wire.connect_calls == 0


# --- writes ---------------------------------------------------------------


async def test_input_register_is_not_writable(client):
    with pytest.raises(ValueError, match="holding"):
        await client.write_register(OUTSIDE_TEMPERATURE, 1)


async def test_unchanged_value_is_not_written_again(client, wire):
    """The pump's EEPROM has a write budget; a value that is already active
    is not written a second time."""
    client.data[PV_SETPOINT] = 42

    assert await client.write_register(PV_SETPOINT, 42) is True

    assert wire.writes == []


async def test_a_changed_value_is_written_and_cached(client, wire):
    client.data[PV_SETPOINT] = 41

    assert await client.write_register(PV_SETPOINT, 42) is True

    assert wire.writes == [(PV_SETPOINT, 42)]
    assert client.data[PV_SETPOINT] == 42


async def test_negative_temperature_write_uses_twos_complement(client, wire):
    """A holding register of TEMPERATURE format; -5.0 °C goes over the wire
    as 65486, not as a negative number pymodbus would reject."""
    address = next(
        address
        for address, item in client._items_dict.items()
        if 40000 <= address < 50000 and item.format == "temperature"
    )

    await client.write_register(address, -50)

    assert wire.writes == [(address, 65486)]


async def test_write_error_is_raised_not_swallowed(client, wire):
    wire.write_error = True

    with pytest.raises(WriteError):
        await client.write_register(PV_SETPOINT, 42)


async def test_write_reconnects_when_the_link_is_down(client, wire):
    wire.connected = False

    assert await client.write_register(PV_SETPOINT, 42) is True
    assert wire.connect_calls == 1


async def test_writes_and_updates_share_one_lock(client, wire):
    """Two coroutines on the wire at once is how transaction ids desync."""
    seen_locked = []

    async def slow_write(address, value, device_id):
        seen_locked.append(client._lock.locked())
        await asyncio.sleep(0)
        return FakeResponse(registers=[value])

    wire.write_register = slow_write

    await client.write_register(PV_SETPOINT, 42)

    assert seen_locked == [True]


async def test_two_clients_do_not_share_their_items(wire):
    """Two heat pumps, two entries, two clients: a register absent on one
    must not read as absent on the other. With the module-level table as the
    only item source, that is exactly what happened (#134)."""
    own_items = [
        ModbusItem(30001, "a", "temperature", "Sensor", "dev_system", "a", batch=30001),
        ModbusItem(30002, "b", "temperature", "Sensor", "dev_system", "b", batch=30001),
    ]
    other_items = [
        ModbusItem(30001, "a", "temperature", "Sensor", "dev_system", "a", batch=30001),
        ModbusItem(30002, "b", "temperature", "Sensor", "dev_system", "b", batch=30001),
    ]
    own = WeishauptModbusClient(host="127.0.0.1", items=own_items)
    own._client = wire
    wire.connected = True
    wire.error_blocks[30001] = ILLEGAL_DATA_ADDRESS
    WeishauptModbusClient(host="127.0.0.2", items=other_items)

    await own.update()

    assert all(item.is_invalid for item in own_items)
    assert not any(item.is_invalid for item in other_items)


def test_the_fake_reports_errors_the_way_pymodbus_does():
    """The double the whole file rests on: isError() follows the exception
    code, and a plain answer carries its registers."""
    assert FakeResponse(exception_code=2).isError() is True
    assert FakeResponse(registers=[1]).isError() is False
