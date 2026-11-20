"""Record the titles a heat pump's web interface shows, for another language or model.

The integration finds its pages and values by their German or English titles,
as a WBB shows them, or by the German ones of an Evoblock. With the controller
set to another language, or on a model whose pages show other values, run this
once and attach the file it writes to an issue: the titles in it are what the
integration has to learn.

    python tools/webif_capture.py <address of the heat pump>

It asks for the web interface's user and password and opens six pages: the
overview, the information menu, the heat pump's main menu, and the three
pages the integration reads on every pump. It finds those entries by their
codes on the controller it was built for, else by their German titles, else
you pick them by number and confirm each pick by its title. Reset, Service
and the time programs are never offered on that controller; on another one it
knows them by their codes only, so check each title before confirming.

Then it opens up to seven more, the pages the integration reads on some
pumps: the first heating circuit's and the second heat generator's
information pages, that circuit's and the hot water's menus and settings
pages, and the hot water modulation. Those it finds by their German titles
alone and leaves out otherwise, never asking: their menus list Reset too,
under codes it does not know. One that does not fit twice is left out too.
It only reads.

The file holds the titles of each page, the values of the pages the
integration reads, the options the modulation offers, and which of the seven
it left out: not shown, or shown in a shape it could not read. On those seven
a value is kept only as a number with its unit; any other text, such as a name
given to a circuit, is written as "(text)". The tool adds no address, user
name, password, session or page address; the values are what those pages show,
so read the file and check it for a serial number, an access code or a network
address before you attach it.

The controller now and then serves a page half or not at all. That does not
end the run: after a minute's rest the tool logs in again and asks only for
what it still lacks, ten times at most. A run takes one to two minutes; one
the controller keeps failing takes up to about an hour before the tool gives
up.

Needs Python 3.10 or newer and a copy of the whole repository: the titles are
read with the integration's own page parser. On Windows, webif_capture.bat
beside it brings a portable Python of its own.
"""

import argparse
from collections import Counter
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
import getpass
from http import HTTPStatus
import http.client
import importlib.util
import ipaddress
import json
import pathlib
import re
import socket
import sys
import threading
import time
from types import ModuleType
from typing import Any
import urllib.parse

# parent.parent rather than parents[1]: a copy of this file alone at the root
# of a drive has no parents[1], and the message below would not be reached.
PAGES_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "custom_components"
    / "weishaupt_modbus"
    / "webif"
    / "pages.py"
)


def _load_pages() -> ModuleType:
    """The integration's parser, so the file shows what the integration sees."""
    if not PAGES_PATH.exists():
        raise SystemExit(
            f"{PAGES_PATH} is missing: run the tool from a copy of the whole repository."
        )
    spec = importlib.util.spec_from_file_location("weishaupt_webif_pages", PAGES_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"{PAGES_PATH} cannot be loaded.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pages = _load_pages()

# Copies of the client's protocol (webif/client.py) and of the titles the
# integration finds its pages by (webif/discovery.py): both need libraries of
# Home Assistant this tool runs without. A test holds the copies equal.
INDEX = "/index.html"
LOGIN = "/login.html"
LOGOUT = "/logout.html"
OVERVIEW = pages.PAGE_PATH
LOGIN_TARGET = "/home.html"
WRONG_PASSWORD = INDEX + "#wrongpassword"
SESSION_COOKIE = "session"
LOGIN_USER_FIELD = "user"
LOGIN_PASSWORD_FIELD = "pass"
TIMEOUT_SECONDS = 20.0
MIN_GAP_SECONDS = 5.0
MAX_PAGE_BYTES = 512 * 1024
INFO = "Info"
HEAT_PUMP = "Wärmepumpe"
STATISTICS = "Statistik"
HEATING = "Heizen"
HEATING_CIRCUIT = re.compile(r"Heizkreis (\d)")
SECOND_GENERATOR = "2. WEZ"
HOT_WATER = "Warmwasser"
SETTINGS = "Einstellungen"
MODULATION_TITLE = "Modulation"
# How the file names a circuit's place in the menus: the lowest one shown.
ANY_CIRCUIT = "Heizkreis n"
# The kind of form the integration reads and sets.
SETTING_LIST = "para_list"
# A value of an optional page kept only as a number with a unit the
# integration reads, a percentage too: any other text, a circuit's name among
# it, is the user's. A test holds the units equal to the integration's.
SHOWN_UNITS = ("°C", "K", "BAR", "KW", "KWh", "m3/h", "rpm", "Hz", "h", "%")
SHOWN_NUMBER = re.compile(
    r"-?\d+(?:\.\d+)?(?: ?(?:"
    + "|".join(re.escape(unit) for unit in sorted(SHOWN_UNITS, key=len, reverse=True))
    + "))?"
)
TEXT_SHOWN = "(text)"
# An optional page that does not fit this often is taken as not shown:
# another model may show it in a shape nobody here has seen.
OPTIONAL_TRIES = 2
# A stack segment starts with the code of its menu entry: the main menu in the
# first two hex digits, the entry in the next six. The rest carries the
# device, the menu depth and at times a shown value.
ITEM_CODE = slice(0, 8)
# Never opened, not even to look: Reset, Service and the time programs, by
# their codes on the controller this was built for. Another model's codes
# are not known, which is why a pick is confirmed.
NEVER_OPENED = frozenset(
    {
        "64004500",
        "32004A00",
        "46006500",
        "C300AC15",
        "58009500",
        "64001100",
        "32004000",
        "46004000",
    }
)
YES = ("y", "yes")

# Restarting a stopped capture by hand, a minute's rest was what the
# controller needed.
RESUME_WAIT_SECONDS = 60.0
MAX_ATTEMPTS = 10
MENU_SEPARATOR = " › "
# The shell's code for a run ended by Ctrl+C.
STOPPED_BY_THE_USER = 130
TRANSPORT_ERRORS = (OSError, http.client.HTTPException)


class Refused(Exception):
    """The controller did not accept the user and the password."""


class Stopped(Exception):
    """An attempt ended where a later one may get further."""


class NoAnswer(Stopped):
    """No answer, an error status or a lost session.

    Nothing more is asked in that attempt: anything asked of a struggling
    controller only adds load.
    """


class Half(Stopped):
    """A page came whole by its status, but without what it has to show."""


@dataclass(frozen=True)
class Step:
    """A page to read.

    The page it is an entry of ("" for the overview), its title there on a
    German controller and its code on the controller this was built for, what
    it is, and how it is read. An optional page is found by its German title
    alone, the lowest numbered circuit's where it is `numbered`, the circuit
    the step `circuit_of` took where it names one, and left out when not
    shown; a `form` is a setting's own page.
    """

    key: str
    parent: str
    german: str
    code: str
    what: str
    menu: bool
    shows_values: bool
    optional: bool = False
    numbered: bool = False
    circuit_of: str = ""
    form: bool = False

    @property
    def values_page(self) -> bool:
        """A page of values, neither a menu nor a form."""
        return not self.menu and not self.form


STEPS = (
    Step("overview", "", "", "", "the overview", menu=True, shows_values=False),
    Step(
        "info",
        "overview",
        INFO,
        "0C000001",
        "the menu of information pages",
        menu=True,
        shows_values=False,
    ),
    Step(
        "heat_pump_menu",
        "overview",
        HEAT_PUMP,
        "64000001",
        "the heat pump's main menu",
        menu=True,
        shows_values=False,
    ),
    Step(
        "heat_pump",
        "info",
        HEAT_PUMP,
        "0C000C22",
        "the heat pump's information page",
        menu=False,
        shows_values=True,
    ),
    Step(
        "statistics",
        "info",
        STATISTICS,
        "0C000C28",
        "the statistics",
        menu=False,
        shows_values=True,
    ),
    Step(
        "heating",
        "heat_pump_menu",
        HEATING,
        "64001800",
        "the heat pump's heating settings",
        menu=True,
        shows_values=True,
    ),
    Step(
        "heating_circuit",
        "info",
        ANY_CIRCUIT,
        "",
        "the first heating circuit's information page",
        menu=False,
        shows_values=True,
        optional=True,
        numbered=True,
    ),
    Step(
        "second_generator",
        "info",
        SECOND_GENERATOR,
        "",
        "the second heat generator's information page",
        menu=False,
        shows_values=True,
        optional=True,
    ),
    Step(
        "modulation",
        "heat_pump_menu",
        MODULATION_TITLE,
        "",
        "the hot water modulation",
        menu=False,
        shows_values=False,
        optional=True,
        form=True,
    ),
    Step(
        "circuit_menu",
        "overview",
        ANY_CIRCUIT,
        "",
        "the first heating circuit's main menu",
        menu=True,
        shows_values=False,
        optional=True,
        circuit_of="heating_circuit",
    ),
    Step(
        "circuit_settings",
        "circuit_menu",
        SETTINGS,
        "",
        "the first heating circuit's settings",
        menu=True,
        shows_values=True,
        optional=True,
    ),
    Step(
        "hot_water_menu",
        "overview",
        HOT_WATER,
        "",
        "the hot water's main menu",
        menu=True,
        shows_values=False,
        optional=True,
    ),
    Step(
        "hot_water_settings",
        "hot_water_menu",
        SETTINGS,
        "",
        "the hot water's settings",
        menu=True,
        shows_values=True,
        optional=True,
    ),
)
STEP_BY_KEY = {step.key: step for step in STEPS}


@dataclass
class _Walk:
    """What a run has learnt so far; it carries over from attempt to attempt.

    The pages read whole, the entry each step reads, which of those the user
    picked, and the optional pages the controller does not show.
    """

    read: dict[str, list[Any]] = field(default_factory=dict)
    chosen: dict[str, Any] = field(default_factory=dict)
    picked: set[str] = field(default_factory=set)
    absent: set[str] = field(default_factory=set)
    misfits: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True)
class Answer:
    """What the controller answered to one request."""

    status: int
    location: str
    text: str
    cookie: str | None


def _allowed(method: str, path: str) -> bool:
    # The access code form on every page saves by GET: an address with any
    # other query could change the pump.
    if method == "POST":
        return path == LOGIN
    if method != "GET":
        return False
    return (
        path in (INDEX, LOGOUT, OVERVIEW)
        or pages.STACK_LINK.fullmatch(path) is not None
    )


def _http_host(host: str) -> str:
    """The address as http.client takes it; a bare IPv6 address needs brackets."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    return f"[{host}]" if address.version == 6 else host


def _relative(location: str) -> str:
    """A redirect's target without scheme and host, as compared."""
    try:
        parts = urllib.parse.urlsplit(location)
    except ValueError:
        return ""
    return urllib.parse.urlunsplit(("", "", parts.path, parts.query, parts.fragment))


def _cut(sockets: list[socket.socket], cut: threading.Event) -> None:
    """End a request that has run out of time, from the timer's thread."""
    cut.set()
    for held in sockets:
        with suppress(OSError):
            held.shutdown(socket.SHUT_RDWR)


def _session_cookie(headers: Iterable[str]) -> str | None:
    """The session id a Set-Cookie header carries, exactly as sent."""
    for header in headers:
        name, _, value = header.split(";", 1)[0].partition("=")
        if name.strip() == SESSION_COOKIE:
            return value.strip()
    return None


class Session:
    """One login, one request at a time, and a gap between any two."""

    def __init__(
        self, host: str, user: str, password: str, sleep: Callable[[float], Any]
    ) -> None:
        """Prepare the session; nothing is sent before the login."""
        self._host = _http_host(host)
        self._form = urllib.parse.urlencode(
            {LOGIN_USER_FIELD: user, LOGIN_PASSWORD_FIELD: password}
        )
        self._sleep = sleep
        self._last: float | None = None
        self._cookie: str | None = None
        self._logged_in = False
        self._struggled = False

    def login(self) -> None:
        """Log in; Refused for wrong credentials, NoAnswer for anything else."""
        index = self._request("GET", INDEX)
        if index.status >= HTTPStatus.BAD_REQUEST:
            raise self._no_answer(f"login page: HTTP {index.status}")
        answer = self._request("POST", LOGIN, self._form)
        if answer.status == HTTPStatus.SEE_OTHER and answer.location == WRONG_PASSWORD:
            raise Refused("the user or the password is wrong")
        accepted = (
            answer.status == HTTPStatus.SEE_OTHER
            and answer.location == LOGIN_TARGET
            and bool(answer.cookie)
        )
        if not accepted:
            # Its database out of reach, an error status, no session: the
            # controller struggles, and the credentials may well be right.
            raise self._no_answer(
                f"login: HTTP {answer.status} to {answer.location or '-'}"
            )
        self._cookie = answer.cookie
        self._logged_in = True

    def page(self, path: str, name: str) -> str:
        """The text of the page at path; NoAnswer unless it came with 200."""
        answer = self._request("GET", path)
        if answer.status != HTTPStatus.OK:
            raise self._no_answer(
                f"{name}: HTTP {answer.status} to {answer.location or '-'}"
            )
        return answer.text

    def logout(self) -> None:
        """Free the session at once, unless the controller struggled."""
        if not self._logged_in or self._struggled:
            return
        try:
            self._request("GET", LOGOUT)
        except NoAnswer:
            print("   no answer to the logout; the controller drops the session itself")

    def _no_answer(self, reason: str) -> NoAnswer:
        self._struggled = True
        return NoAnswer(reason)

    def _request(self, method: str, path: str, form: str | None = None) -> Answer:
        if not _allowed(method, path):
            raise ValueError(f"{method} {path} is not on the positive list")
        if self._last is not None:
            self._sleep(max(0.0, self._last + MIN_GAP_SECONDS - time.monotonic()))
        headers = {"Connection": "close"}
        if self._cookie:
            headers["Cookie"] = f"{SESSION_COOKIE}={self._cookie}"
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        connection = http.client.HTTPConnection(self._host, timeout=TIMEOUT_SECONDS)
        # The socket's time limit bounds each read, not the request: a server
        # sending a byte now and then would keep one request open for good.
        sockets: list[socket.socket] = []
        cut = threading.Event()
        deadline = threading.Timer(TIMEOUT_SECONDS, _cut, (sockets, cut))
        deadline.start()
        try:
            connection.connect()
            # Held here: http.client lets go of it once a closing answer begins.
            sockets.append(connection.sock)
            if cut.is_set():
                # The deadline fired before the socket was held and shut nothing.
                _cut(sockets, cut)
            connection.request(method, path, body=form, headers=headers)
            response = connection.getresponse()
            body = response.read(MAX_PAGE_BYTES + 1)
        except TRANSPORT_ERRORS as error:
            raise self._no_answer(f"no answer ({type(error).__name__})") from error
        finally:
            deadline.cancel()
            connection.close()
            self._last = time.monotonic()
        if cut.is_set():
            raise self._no_answer(f"no whole answer within {TIMEOUT_SECONDS:.0f} s")
        if len(body) > MAX_PAGE_BYTES:
            raise self._no_answer(f"an answer of more than {MAX_PAGE_BYTES} bytes")
        return Answer(
            response.status,
            _relative(response.getheader("Location", "")),
            body.decode("utf-8", errors="replace"),
            _session_cookie(response.headers.get_all("Set-Cookie") or []),
        )


def _path(step: Step, title: Callable[[Step], str]) -> str:
    """The step's place in the menus, "Info › Statistik", by `title` of each step."""
    titles: list[str] = []
    while step.parent:
        titles.insert(0, title(step))
        step = STEP_BY_KEY[step.parent]
    return MENU_SEPARATOR.join(titles)


def _number(answer: str, count: int) -> int | None:
    """The list position an answer names, or None for anything else."""
    answer = answer.strip()
    if answer.isascii() and answer.isdigit() and 1 <= int(answer) <= count:
        return int(answer) - 1
    return None


def _pick(step: Step, offered: list[Any], ask: Callable[[str], str]) -> Any:
    """The entry the user names by its number and confirms by its title."""
    print(f"\nWhich of these is {step.what}?")
    for number, entry in enumerate(offered, start=1):
        print(f"  {number}. {entry.title}")
    while True:
        answer = ask(f"Number of {step.what} (German: {step.german}): ")
        position = _number(answer, len(offered))
        if position is None:
            print(f"   A number from 1 to {len(offered)}, please.")
            continue
        entry = offered[position]
        if ask(f"Open '{entry.title}'? [y/N] ").strip().lower() in YES:
            return entry


def _item(href: str) -> str:
    """The code of the menu entry a page address leads to."""
    return href.split(pages.STACK_QUERY, 1)[1].rsplit(",", maxsplit=1)[-1][ITEM_CODE]


def _titled(step: Step, offered: list[Any], walk: _Walk) -> list[Any]:
    """The entries under the step's German title; the lowest circuit's only.

    Info lists the circuits switched on; the overview may show one more,
    switched off, whose settings would stand in for the first circuit's.
    """
    if step.circuit_of:
        circuit = walk.chosen.get(step.circuit_of)
        if circuit is None:
            return []
        return [entry for entry in offered if entry.title == circuit.title]
    if not step.numbered:
        return [entry for entry in offered if entry.title == step.german]
    circuits = [
        (int(match.group(1)), entry)
        for entry in offered
        if (match := HEATING_CIRCUIT.fullmatch(entry.title))
    ]
    if not circuits:
        return []
    lowest = min(number for number, _ in circuits)
    return [entry for number, entry in circuits if number == lowest]


def _entry(step: Step, walk: _Walk, ask: Callable[[str], str]) -> Any:
    """The entry of its parent page the step reads; None for an optional one not shown.

    Found by its code on the controller this was built for, else by its German
    title, else picked by the user; an entry the tool never opens is never
    offered, and an optional page never picked. Found once: a later attempt
    goes the same way, unless a picked page came in a shape that does not fit.
    """
    if step.key in walk.chosen:
        return walk.chosen[step.key]
    offered = [
        entry
        for entry in walk.read[step.parent]
        if entry.href is not None and _item(entry.href) not in NEVER_OPENED
    ]
    if not offered and step.optional:
        return None
    if not offered:
        # Kept, the parent would offer nothing in every later attempt too;
        # picked by the user, it was likely the wrong pick.
        del walk.read[step.parent]
        _drop_pick(walk, step.parent)
        raise Half(f"{STEP_BY_KEY[step.parent].what} came with nothing to open")
    # An entry sharing its link with another may lead to either page.
    links = Counter(entry.href for entry in walk.read[step.parent] if entry.href)
    clear = [entry for entry in offered if links[entry.href] == 1]
    by_code = [entry for entry in clear if _item(entry.href) == step.code]
    by_title = _titled(step, clear, walk)
    found = by_code if len(by_code) == 1 else by_title
    if len(found) == 1:
        walk.chosen[step.key] = found[0]
    elif step.optional:
        return None
    else:
        walk.chosen[step.key] = _pick(step, offered, ask)
        walk.picked.add(step.key)
    return walk.chosen[step.key]


def _entries(step: Step, text: str, path: str) -> list[Any]:
    """What the page shows: menus, entries, values, or its form's options.

    The main menus on the overview, the entries one level down on a menu,
    and the options its own form offers on a setting's page.
    """
    if step.form:
        form = pages.setting_form(text)
        # Another setting's form served in its place would teach its options.
        if form is None or form.path != path or form.type != SETTING_LIST:
            return []
        return list(form.offered)
    if not step.parent:
        return [pages.Entry(title, "", href) for title, href in pages.main_menus(text)]
    if step.menu:
        return pages.children(text, path)
    return [pages.Entry(title, shown, None) for title, shown in pages.values(text)]


def _whole(step: Step, entries: list[Any]) -> bool:
    """Something shown, and a value beside every entry where values belong."""
    if not entries:
        return False
    # Nobody here has seen every row of the optional pages: one shown without
    # a value would fail every attempt and with it the whole run.
    return (
        step.optional or not step.shows_values or all(entry.text for entry in entries)
    )


def _twin(step: Step, entries: list[Any], walk: _Walk) -> str | None:
    """Another page of values already read with the very same titles, if any.

    The controller now and then serves one section's values in place of
    another's, whole by every check of its own; which of the two came right
    cannot be told.
    """
    if not step.values_page:
        return None
    titles = {entry.title for entry in entries}
    twins = [
        other.key
        for other in STEPS
        if other.values_page
        and other.key in walk.read
        and {entry.title for entry in walk.read[other.key]} == titles
    ]
    return twins[0] if twins else None


def _drop_pick(walk: _Walk, key: str) -> None:
    # A picked page that does not fit is more likely a wrong pick than a
    # hiccup: kept, it would be asked for the same way in every attempt.
    if key in walk.picked:
        walk.picked.discard(key)
        del walk.chosen[key]


def _read_missing(session: Session, walk: _Walk, ask: Callable[[str], str]) -> None:
    """Read every page not yet read whole, in the order of the menus."""
    for step in STEPS:
        if step.key in walk.read or step.key in walk.absent:
            continue
        path = OVERVIEW
        if step.parent:
            # The steps come after their parents: an absent parent is decided.
            entry = None if step.parent in walk.absent else _entry(step, walk, ask)
            if entry is None:
                walk.absent.add(step.key)
                continue
            path = entry.href
        entries = _entries(step, session.page(path, step.key), path)
        if not _whole(step, entries) and _gives_up(step, walk):
            walk.absent.add(step.key)
            continue
        if not _whole(step, entries):
            _drop_pick(walk, step.key)
            raise Half(f"{step.what} came half")
        twin = _twin(step, entries, walk)
        if twin is not None and _gives_up(step, walk):
            walk.absent.add(step.key)
            continue
        if twin is not None and step.optional:
            # Which of the two came right cannot be told, but only the
            # optional one may go: a page read whole stays.
            raise Half(f"{step.what} showed the values of another page")
        if twin is not None:
            del walk.read[twin]
            _drop_pick(walk, step.key)
            _drop_pick(walk, twin)
            raise Half(f"{step.what} showed the values of another page")
        walk.read[step.key] = entries
        print(f"   {step.what}: {len(entries)} entries")


def _gives_up(step: Step, walk: _Walk) -> bool:
    """An optional page that did not fit once more than it may."""
    if not step.optional:
        return False
    walk.misfits[step.key] += 1
    return walk.misfits[step.key] >= OPTIONAL_TRIES


def _kept(step: Step, entries: list[Any]) -> dict[str, Any]:
    """What the file keeps of a page: values only where the integration reads them."""
    if step.form:
        # The options alone: the one set is the user's, not a title.
        return {"offered": entries}
    if step.shows_values and step.optional:
        return {"entries": [[entry.title, _neutral(entry.text)] for entry in entries]}
    if step.shows_values:
        return {"entries": [[entry.title, entry.text] for entry in entries]}
    return {"titles": [entry.title for entry in entries]}


def _neutral(text: str) -> str:
    """A number with its unit as shown, anything else as text alone."""
    if not text or SHOWN_NUMBER.fullmatch(text):
        return text
    return TEXT_SHOWN


def _report(walk: _Walk) -> dict[str, Any]:
    return {
        "pages": [
            {
                "page": step.key,
                "german": _path(step, lambda each: each.german),
                "shown": _path(step, lambda each: walk.chosen[each.key].title),
                **_kept(step, walk.read[step.key]),
            }
            for step in STEPS
            if step.key in walk.read
        ],
        "absent": [
            _path(step, lambda each: each.german)
            for step in STEPS
            if step.key in walk.absent
        ],
    }


def capture(
    host: str,
    user: str,
    password: str,
    ask: Callable[[str], str],
    *,
    attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], Any] = time.sleep,
) -> dict[str, Any] | None:
    """The pages' titles and values, or None when they could not be had."""
    walk = _Walk()
    for attempt in range(1, attempts + 1):
        session = Session(host, user, password, sleep)
        try:
            session.login()
            _read_missing(session, walk, ask)
        except Refused as error:
            print(f"Login refused: {error}.")
            return None
        except Stopped as error:
            print(f"Attempt {attempt} of {attempts} stopped: {error}.")
        else:
            return _report(walk)
        finally:
            session.logout()
        if attempt < attempts:
            print(f"   Going on in {RESUME_WAIT_SECONDS:.0f} s.")
            sleep(RESUME_WAIT_SECONDS)
    print(f"Gave up after {attempts} attempts; try again later.")
    return None


def _address(text: str) -> str:
    """The heat pump's address alone, as the integration takes it; no URL."""
    if "/" in text:
        raise argparse.ArgumentTypeError(
            "give the address alone, like 192.0.2.10 or a name, not a web address"
        )
    # The prompt keeps a pasted space; the integration strips it too.
    address = text.strip()
    if not address or any(character.isspace() for character in address):
        raise argparse.ArgumentTypeError(
            "give the address of the heat pump, like 192.0.2.10 or a name"
        )
    return address


def main(argv: list[str] | None = None) -> int:
    """Ask for the credentials, read the pages, and save the file."""
    # A console that cannot show a title must not end the run.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "host", type=_address, help="the heat pump's address, as in the integration"
    )
    arguments = parser.parse_args(argv)
    try:
        user = input("Web interface user: ").strip()
        password = getpass.getpass("Web interface password: ")
        report = capture(arguments.host, user, password, input)
    except KeyboardInterrupt:
        # A session it was logged in to was logged out on the way here.
        print("\nStopped.")
        return STOPPED_BY_THE_USER
    if report is None:
        return 1
    target = pathlib.Path(f"webif_titles_{time.strftime('%Y%m%d_%H%M%S')}.json")
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Saved {target.resolve()}. Read it, then attach it to the issue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
