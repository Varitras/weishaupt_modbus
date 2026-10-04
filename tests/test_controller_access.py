"""Every request to the controller holds the lock its other requests take.

The README promises that the web interface never asks the heat pump at the
same moment as a Modbus request from this integration. Each side keeps
that by taking `host_lock` around every request it sends, and nothing but
this guard notices a new request that does not: the pump dialog's Modbus
probe read a register without it, beside a web interface polling the same
controller, and every test passed.

The check is lexical and per function: the lock has to be taken in the
function that sends, where a reader sees it. A nested function does not
inherit it, since it may run after the lock is released. A request wrapped
in another awaited call, a `wait_for` or a `gather`, counts all the same. A
request that is not awaited where it is made - bound to a name, gathered
from a list, handed to a task, taken as an alias - is refused outright: when
it goes out, no lexical check can tell. The positive list of the web
interface is held to the same place, as a refusal before the request, in
the sending function itself and about the names the request is sent with.
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
# The coordinators have a diagnostics() of their own under the library's name
# for a request.
OWN_NAMES = frozenset({"diagnostics"})
# An HTTP verb is a request on a session (`self._session`,
# `async_get_clientsession(hass)`) or on aiohttp itself. On anything else,
# `get` and `options` are a dict's and a config entry's.
SESSION = "session"
HTTP_MODULE = "aiohttp"


def _awaited_calls(tree):
    """Every call in what is awaited or entered by `async with`, also one
    wrapped in another call there (`wait_for`, `gather`, `shield`); whether
    the function it is in holds the lock there, and that function."""
    found = {}

    def note(expression, held, function):
        for call in ast.walk(expression):
            if isinstance(call, ast.Call):
                found.setdefault(call, (call, held, function))

    def visit(node, held, function):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            held, function = False, node
        if isinstance(node, ast.Await):
            note(node.value, held, function)
        if isinstance(node, ast.AsyncWith):
            for item in node.items:
                visit(item.context_expr, held, function)
                note(item.context_expr, held, function)
                held = held or _is_host_lock(item.context_expr)
            for statement in node.body:
                visit(statement, held, function)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, held, function)

    visit(tree, False, None)
    return list(found.values())


def _name_of(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_host_lock(expression):
    """`self._host_lock`, `host_lock(hass, host)`: a name that ends in it.
    One that merely contains it is another lock."""
    target = expression.func if isinstance(expression, ast.Call) else expression
    name = _name_of(target)
    return name is not None and name.endswith(LOCK)


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


def _refuses_off_the_list(node, sent_with):
    """`if not _allowed(method, path):` with a raise in its body, asking
    about two names the request is sent with."""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    asks = (
        isinstance(test, ast.UnaryOp)
        and isinstance(test.op, ast.Not)
        and isinstance(test.operand, ast.Call)
        and _name_of(test.operand.func) == POSITIVE_LIST
    )
    if not asks:
        return False
    asked = test.operand.args
    about_the_request = len(asked) == 2 and all(
        isinstance(argument, ast.Name) and argument.id in sent_with
        for argument in asked
    )
    return about_the_request and any(
        isinstance(statement, ast.Raise) for statement in node.body
    )


def _refused_off_the_list_first(function, call):
    """As a statement of the sending function itself, not on one branch of
    it, and before the request."""
    if function is None:
        return False
    sent_with = {
        node.id
        for argument in call.args
        for node in ast.walk(argument)
        if isinstance(node, ast.Name)
    }
    return any(
        _refuses_off_the_list(statement, sent_with) and statement.lineno < call.lineno
        for statement in function.body
    )


def _unlisted_web_requests(source):
    return [
        where
        for where, call, _, function in _requests(source)
        if call.func.attr in WEB_REQUESTS
        and not _refused_off_the_list_first(function, call)
    ]


def _is_session(node):
    target = node.func if isinstance(node, ast.Call) else node
    name = _name_of(target) or ""
    return name.lower().endswith(SESSION) or name == HTTP_MODULE


def _names_a_request(attribute):
    """A request method where it is taken from what sends it: the library's
    names on anything but the heat pump, an HTTP verb on a session."""
    if attribute.attr in WEB_REQUESTS:
        return _is_session(attribute.value)
    if attribute.attr in OWN_NAMES:
        return False
    by_the_heat_pump = (
        _name_of(attribute.value) == DEVICE_ATTRIBUTE
        and attribute.attr in DEVICE_METHODS
    )
    return attribute.attr in MODBUS_REQUESTS and not by_the_heat_pump


def _named_requests(source):
    return [
        f"{node.lineno}: {ast.unparse(node)}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute) and _names_a_request(node)
    ]


def _awaited_nodes(tree):
    """Every node of what is awaited or entered by `async with`."""
    inside = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Await):
            inside.update(map(id, ast.walk(node.value)))
        if isinstance(node, ast.AsyncWith):
            for item in node.items:
                inside.update(map(id, ast.walk(item.context_expr)))
    return inside


def _requests_not_awaited_where_made(source):
    """Requests made but not awaited there, and request methods taken without
    a call: bound to a name, gathered from a list, handed to a task, an
    alias. When those go out, no lexical check can tell."""
    tree = ast.parse(source)
    awaited = _awaited_nodes(tree)
    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    return [
        f"{node.lineno}: {ast.unparse(node)}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and _names_a_request(node)
        and not (id(node) in called and id(node) in awaited)
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


def test_the_positive_list_has_to_refuse_not_just_be_asked():
    """A call whose answer nobody looks at passed for the check."""
    asked_and_ignored = (
        "async def _exchange(self, method, path):\n"
        "    _allowed(method, path)\n"
        "    async with self._session.request(method, path) as answer:\n"
        "        return answer\n"
    )

    assert _unlisted_web_requests(asked_and_ignored) == ["3: self._session.request"]


def test_the_scan_sees_a_request_wrapped_in_another_call():
    """A time limit or a gathering around the request hid it, and `wait_for`
    is the shape a time limit would come back in."""
    wrapped = (
        "async def probe(unit):\n"
        "    await asyncio.wait_for(unit.read_input_registers(30001, 1), 5)\n"
    )
    gathered = (
        "async def poll(first, second):\n"
        "    await asyncio.gather(first.async_update(), second.async_update())\n"
    )

    assert _unlocked_requests(wrapped) == ["2: unit.read_input_registers"]
    assert _unlocked_requests(gathered) == [
        "2: first.async_update",
        "2: second.async_update",
    ]


def test_a_lock_that_only_contains_the_name_is_another_lock():
    another = (
        "async def exchange(self):\n"
        "    async with self._host_lock_for_tests:\n"
        "        await self._session.request(method, url)\n"
    )

    assert _unlocked_requests(another) == ["3: self._session.request"]


def test_a_request_has_to_be_awaited_where_it_is_made():
    """Made under the lock and sent after it, gathered from a list made
    earlier, handed to a task or taken as an alias: the scan could not tell
    when such a request goes out, and passed every one of them."""
    made_under_the_lock = (
        "async def write(self, component, word):\n"
        "    async with self._host_lock:\n"
        "        writing = component.write('field', word)\n"
        "    await writing\n"
    )
    gathered = (
        "async def poll(self, components):\n"
        "    updates = [component.async_update() for component in components]\n"
        "    async with self._host_lock:\n"
        "        await asyncio.gather(*updates)\n"
    )
    handed_to_a_task = (
        "async def probe(self, unit):\n"
        "    reading = asyncio.create_task(unit.read_input_registers(30001, 1))\n"
        "    async with self._host_lock:\n"
        "        await reading\n"
    )
    aliases = (
        "async def ask(self, unit, url):\n"
        "    read = unit.read_input_registers\n"
        "    get = self._session.get\n"
        "    async with self._host_lock:\n"
        "        await read(30001, 1)\n"
        "        await get(url)\n"
    )

    assert _requests_not_awaited_where_made(made_under_the_lock) == [
        "3: component.write"
    ]
    assert _requests_not_awaited_where_made(gathered) == ["2: component.async_update"]
    assert _requests_not_awaited_where_made(handed_to_a_task) == [
        "2: unit.read_input_registers"
    ]
    assert sorted(_requests_not_awaited_where_made(aliases)) == [
        "2: unit.read_input_registers",
        "3: self._session.get",
    ]


def test_what_only_shares_a_request_s_name_is_no_request():
    """Off a session, `get` and `options` are a dict's and a config entry's;
    the coordinators have a diagnostics() of their own, and the heat pump's
    methods take the lock inside."""
    shared_names = (
        "def read(self, entry, data, coordinator):\n"
        "    interval = entry.options.get('interval', data.get('default'))\n"
        "    return coordinator.diagnostics(), interval\n"
    )
    heat_pump = (
        "async def poll(self):\n"
        "    update = self.device.async_update\n"
        "    await update()\n"
    )

    assert _requests_not_awaited_where_made(shared_names) == []
    assert _requests_not_awaited_where_made(heat_pump) == []


def test_every_request_is_awaited_where_it_is_made():
    made_elsewhere = _in_package(_requests_not_awaited_where_made)

    assert _in_package(_named_requests), "no request found - the scan looks nowhere"
    assert not made_elsewhere, (
        f"request(s) not awaited where they are made: {made_elsewhere}. Await "
        "each request in the statement that makes it, under the lock."
    )


def test_the_positive_list_has_to_ask_about_the_request_itself():
    """Asked on one branch only, or about another address than the one the
    request goes to, the refusal passed for the check."""
    on_one_branch = (
        "async def _exchange(self, method, path):\n"
        "    if method == 'POST':\n"
        "        if not _allowed(method, path):\n"
        "            raise ValueError(path)\n"
        "    async with self._session.request(method, self._base + path) as answer:\n"
        "        return answer\n"
    )
    about_another_address = (
        "async def _exchange(self, method, path):\n"
        "    if not _allowed(method, INDEX):\n"
        "        raise ValueError(path)\n"
        "    async with self._session.request(method, self._base + path) as answer:\n"
        "        return answer\n"
    )
    about_the_request = (
        "async def _exchange(self, method, path):\n"
        "    if not _allowed(method, path):\n"
        "        raise ValueError(path)\n"
        "    async with self._session.request(method, self._base + path) as answer:\n"
        "        return answer\n"
    )

    assert _unlisted_web_requests(on_one_branch) == ["5: self._session.request"]
    assert _unlisted_web_requests(about_another_address) == ["4: self._session.request"]
    assert _unlisted_web_requests(about_the_request) == []
