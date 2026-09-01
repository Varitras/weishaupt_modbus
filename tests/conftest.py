"""Shared pytest configuration.

Declares the Home Assistant custom-component test plugin (the `hass` fixture
and a matching Home Assistant install), keeps every test off real hardware,
and holds the runtime budget per test - see durations.py for why one exists.
"""

from pymodbus.client import AsyncModbusTcpClient
import pytest

import custom_components.weishaupt_modbus as integration

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


@pytest.fixture(autouse=True)
def _no_real_heat_pump(monkeypatch):
    """Nothing in the suite may open a TCP connection to a heat pump.

    Global on purpose: a protection whose absence is silent belongs where every
    module gets it. Blocks the TRANSPORT rather than the client's methods -
    the client's own connect/backoff logic is under test, and stubbing it out
    would disable the logic instead of the traffic. A test that installs its
    own fake pymodbus client on the instance is unaffected.

    The WebIF connection is refused at construction: no test configures it,
    so one that reaches it has taken a path it did not mean to.
    """

    async def _refuse(self, *_args, **_kwargs):
        raise AssertionError(
            "a test reached a real Modbus device - install a fake pymodbus "
            "client on the WeishauptModbusClient instance instead"
        )

    class _NoWebIf:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError(
                "a test constructed a real WebIF connection - stub WebifConnection "
                "in custom_components.weishaupt_modbus instead"
            )

    monkeypatch.setattr(AsyncModbusTcpClient, "connect", _refuse)
    monkeypatch.setattr(integration, "WebifConnection", _NoWebIf)
