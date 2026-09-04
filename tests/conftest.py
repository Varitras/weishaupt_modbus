"""Shared pytest configuration.

Declares the Home Assistant custom-component test plugin (the `hass` fixture
and a matching Home Assistant install), keeps every test off real hardware,
and holds the runtime budget per test - see durations.py for why one exists.
"""

from modbus_connection import IllegalDataAddressError
from modbus_connection.mock import MockModbusConnection
import pytest

from .durations import SLOW_TEST_SECONDS, over_budget

pytest_plugins = ("pytest_homeassistant_custom_component",)

# Summed per test across setup, call and teardown, and read at the end of the
# session. A dict at module level because that is what a pytest hook has: the
# hooks are functions, not a fixture with somewhere to keep state.
_durations: dict = {}


def pytest_addoption(parser):
    parser.addoption(
        "--slow-test-seconds",
        type=float,
        default=SLOW_TEST_SECONDS,
        help=(
            "fail the session if a single test takes longer than this "
            "(0 makes every test late, which is how the check is tested)"
        ),
    )


def pytest_runtest_logreport(report):
    """Add up what one test costs, fixtures included."""
    _durations[report.nodeid] = _durations.get(report.nodeid, 0.0) + report.duration


def pytest_sessionfinish(session, exitstatus):
    """Turn a green run red when a test ran far longer than it should.

    Only a green one: a failing suite has more urgent news, and a test that is
    slow *because* it failed is not the subject here.
    """
    if exitstatus != pytest.ExitCode.OK:
        return

    late = over_budget(_durations, session.config.getoption("--slow-test-seconds"))
    if not late:
        return

    listed = "\n  ".join(f"{seconds:7.2f}s {node_id}" for node_id, seconds in late)
    print(
        f"\nSLOWER THAN THE BUDGET ALLOWS:\n  {listed}\n\n"
        "A test in the minutes is nearly always a wait that was meant to be "
        "shortened and no longer is - check what the test patches against "
        "where the production code now reads it. If the time is genuinely "
        "warranted, raise SLOW_TEST_SECONDS in tests/durations.py and say "
        "in the commit why."
    )
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


# Where Home Assistant's modbus integration builds the connection it shares;
# the mock goes in there, so the whole path from async_get_unit down is real.
CORE_CONNECTION = "homeassistant.components.modbus.connection.ModbusConnection"


@pytest.fixture(autouse=True)
def _no_real_heat_pump(monkeypatch):
    """Nothing in the suite may open a TCP connection to a heat pump.

    Global on purpose: a protection whose absence is silent belongs where every
    module gets it. A test that wants a pump asks for `mock_modbus`.
    """

    def _refuse(params, **_kwargs):
        raise AssertionError(
            "a test reached a real Modbus device - use the mock_modbus fixture"
        )

    monkeypatch.setattr(CORE_CONNECTION, _refuse)


class SharedMockModbus:
    """Stands in for the core modbus integration's connection factory.

    The hub closes the shared connection when the last entry lets go of it
    and builds a new one on the next load, so every call hands out a fresh
    in-memory connection - seeded with the same registers and failures, the
    way the real pump is still the same pump after a reload.
    """

    def __init__(self) -> None:
        self.params_seen: list = []
        self.connections: list[MockModbusConnection] = []
        self._raw: dict = {"input": {}, "holding": {}}
        self._request_failure: Exception | None = None
        self._read_failures: list = []

    def __call__(self, params, **_kwargs) -> MockModbusConnection:
        self.params_seen.append(params)
        connection = MockModbusConnection()
        unit = connection.for_unit(1)
        unit.load_raw(self._raw)
        unit.fail_requests(self._request_failure)
        for address, error in self._read_failures:
            unit.fail_read(address, error, register_type="input")
        self.connections.append(connection)
        return connection

    @property
    def unit(self):
        """The unit of the connection currently handed out."""
        return self.connections[-1].for_unit(1)

    @property
    def connected(self) -> bool:
        return bool(self.connections) and self.connections[-1].connected

    def load_raw(self, raw: dict) -> None:
        for space, values in raw.items():
            self._raw[space].update(values)
        for connection in self.connections:
            connection.for_unit(1).load_raw(raw)

    def fail_read_band(self, address: int) -> None:
        """The controller refuses the input band starting at ``address``."""
        self._read_failures.append((address, IllegalDataAddressError()))
        for connection in self.connections:
            connection.for_unit(1).fail_read(
                address, IllegalDataAddressError(), register_type="input"
            )

    def fail_requests(self, error: Exception | None) -> None:
        self._request_failure = error
        for connection in self.connections:
            connection.for_unit(1).fail_requests(error)


@pytest.fixture
def mock_modbus(monkeypatch):
    """The connection the core modbus integration hands out, in memory.

    `params_seen` records every connection the integration asked for - one
    per (re)load, since the last unit released closes the shared one.
    """
    shared = SharedMockModbus()
    monkeypatch.setattr(CORE_CONNECTION, shared)
    return shared
