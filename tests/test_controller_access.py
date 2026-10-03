"""Every request to the controller holds the lock its other requests take.

The README promises that the web interface never asks the heat pump at the
same moment as a Modbus request from this Home Assistant. Each side keeps
that by taking `host_lock` around every request it sends, and nothing but
this guard notices a new request that does not: the pump dialog's Modbus
probe read a register without it, beside a web interface polling the same
controller, and every test passed.

The check is lexical and per function: the lock has to be taken in the
function that sends, where a reader sees it. A nested function does not
inherit it, since it may run after the lock is released. The positive list
of the web interface is held to the same place.
"""

import ast
import inspect
import pathlib

from modbus_connection import ModbusUnit
from modbus_connection.model import Component, ComponentGroup

from custom_components.weishaupt_modbus.weishaupt_modbus_api.device import (
    WeishauptHeatPump,
)

PACKAGE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "weishaupt_modbus"
)
LOCK = "host_lock"
POSITIVE_LIST = "_allowed"


def _awaitable_methods(*classes):
    return frozenset(
        name
        for cls in classes
        for name, _ in inspect.getmembers(cls, inspect.iscoroutinefunction)
        if not name.startswith("_")
    )


# Read from the library, so a request it adds is covered as well.
MODBUS_REQUESTS = _awaitable_methods(ModbusUnit, Component, ComponentGroup)
WEB_REQUESTS = frozenset(
    {
        "request",
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "head",
        "options",
        "ws_connect",
    }
)
# The integration's heat pump has the library's names for its own methods and
# takes the lock inside them, around each band read and each write; those
# requests are checked here like any other.
DEVICE_ATTRIBUTE = "device"
DEVICE_METHODS = _awaitable_methods(WeishauptHeatPump)


def _awaited_calls(tree):
    """Every call that is awaited or entered by `async with`, whether the
    function it is in holds the lock there, and that function."""
    found = []

    def visit(node, held, function):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            held, function = False, node
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            found.append((node.value, held, function))
        if isinstance(node, ast.AsyncWith):
            for item in node.items:
                visit(item.context_expr, held, function)
                if isinstance(item.context_expr, ast.Call):
                    found.append((item.context_expr, held, function))
                held = held or LOCK in ast.unparse(item.context_expr)
            for statement in node.body:
                visit(statement, held, function)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, held, function)

    visit(tree, False, None)
    return found


def _name_of(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_request(call):
    method = call.func
    if not isinstance(method, ast.Attribute):
        return False
    if method.attr in WEB_REQUESTS:
        return True
    by_the_heat_pump = (
        _name_of(method.value) == DEVICE_ATTRIBUTE and method.attr in DEVICE_METHODS
    )
    return method.attr in MODBUS_REQUESTS and not by_the_heat_pump


def _requests(source):
    """Each request in source: where it is, the call, whether it holds the
    lock, and the function it is in."""
    return [
        (f"{call.lineno}: {ast.unparse(call.func)}", call, held, function)
        for call, held, function in _awaited_calls(ast.parse(source))
        if _is_request(call)
    ]


def _all_requests(source):
    return [where for where, *_ in _requests(source)]


def _unlocked_requests(source):
    return [where for where, _, held, _ in _requests(source) if not held]


def _web_requests(source):
    return [
        where for where, call, *_ in _requests(source) if call.func.attr in WEB_REQUESTS
    ]


def _asks_the_positive_list_first(function, call):
    return function is not None and any(
        isinstance(node, ast.Call)
        and _name_of(node.func) == POSITIVE_LIST
        and node.lineno < call.lineno
        for node in ast.walk(function)
    )


def _unlisted_web_requests(source):
    return [
        where
        for where, call, _, function in _requests(source)
        if call.func.attr in WEB_REQUESTS
        and not _asks_the_positive_list_first(function, call)
    ]


def _in_package(check):
    """`check` over every module of the package, each finding with its module."""
    return [
        f"{source.relative_to(PACKAGE).as_posix()}:{where}"
        for source in sorted(PACKAGE.rglob("*.py"))
        for where in check(source.read_text(encoding="utf-8"))
    ]


def test_every_request_to_the_controller_holds_the_host_lock():
    unlocked = _in_package(_unlocked_requests)

    assert _in_package(_all_requests), "no request found - the scan looks nowhere"
    assert not unlocked, (
        f"request(s) to the controller outside the host lock: {unlocked}. Take "
        "`host_lock(hass, host)` around it in the function that sends it."
    )


def test_every_web_request_asks_the_positive_list_first():
    """GET and the login POST, nothing else, and asked where the request
    goes out: a check further up lets a second way out pass it by."""
    unlisted = _in_package(_unlisted_web_requests)

    assert _in_package(_web_requests), "no web request found - the scan looks nowhere"
    assert not unlisted, (
        f"web request(s) sent without the positive list: {unlisted}. Ask "
        f"`{POSITIVE_LIST}(method, path)` first, in the function that sends."
    )


def test_the_scan_catches_the_probe_that_shipped():
    """Verbatim from the incident, and the shape that replaced it."""
    shipped = (
        "async def pump_answers(hass, data):\n"
        "    async with async_get_temporary_unit(hass, params, MODBUS_UNIT_ID) as unit:\n"
        "        await unit.read_input_registers(PROBE_REGISTER, 1)\n"
    )
    replaced = (
        "async def pump_answers(hass, data):\n"
        "    async with (\n"
        "        host_lock(hass, data[CONF.HOST]),\n"
        "        async_get_temporary_unit(hass, params, MODBUS_UNIT_ID) as unit,\n"
        "    ):\n"
        "        await unit.read_input_registers(PROBE_REGISTER, 1)\n"
    )

    assert _unlocked_requests(shipped) == ["3: unit.read_input_registers"]
    assert _unlocked_requests(replaced) == []


def test_the_lock_counts_only_where_the_request_goes_out():
    """Entered after the request, or taken around a function defined for
    later, the lock is not held when the request is sent."""
    entered_after = (
        "async def exchange(self):\n"
        "    async with self._session.request(method, url) as answer, self._host_lock:\n"
        "        pass\n"
    )
    nested = (
        "async def poll(self):\n"
        "    async with self._host_lock:\n"
        "        async def later():\n"
        "            await component.async_update()\n"
    )

    assert _unlocked_requests(entered_after) == ["2: self._session.request"]
    assert _unlocked_requests(nested) == ["4: component.async_update"]


def test_only_the_heat_pump_s_own_methods_go_out_unlocked():
    """It takes the lock inside them; a library request on it does not."""
    own = "async def poll(self):\n    await self.device.async_update()\n"
    library = (
        "async def probe(self):\n    await self.device.read_input_registers(1, 1)\n"
    )

    assert _unlocked_requests(own) == []
    assert _unlocked_requests(library) == ["2: self.device.read_input_registers"]


def test_the_scan_catches_a_positive_list_asked_one_call_up():
    """The shape the client had: checked in the caller, sent in the callee."""
    asked_by_the_caller = (
        "async def _request(self, method, path):\n"
        "    if not _allowed(method, path):\n"
        "        raise ValueError(path)\n"
        "    return await self._exchange(method, path)\n"
        "async def _exchange(self, method, path):\n"
        "    async with self._session.request(method, path) as answer:\n"
        "        return answer\n"
    )
    asked_where_it_goes_out = (
        "async def _exchange(self, method, path):\n"
        "    if not _allowed(method, path):\n"
        "        raise ValueError(path)\n"
        "    async with self._session.request(method, path) as answer:\n"
        "        return answer\n"
    )

    assert _unlisted_web_requests(asked_by_the_caller) == ["6: self._session.request"]
    assert _unlisted_web_requests(asked_where_it_goes_out) == []
