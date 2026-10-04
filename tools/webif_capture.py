"""Record the titles a heat pump's web interface shows, for another language.

The integration finds its pages and values by their German titles. With the
controller set to another language, run this once and attach the file it
writes to an issue: the titles in it are what the integration has to learn.

    python tools/webif_capture.py <address of the heat pump>

It asks for the web interface's user and password and opens six pages, no
more: the overview, the information menu, the heat pump's main menu, and the
three pages the integration reads. You pick those entries by their number;
on a German controller it finds them by itself. It only reads, and it never
opens any other entry - the heat pump's main menu also lists Reset.

The file holds each page's titles and the values shown beside them, nothing
else: no address, no user name, no password, no session, no page address.
Read it before you attach it.

The controller now and then serves a page half or not at all. That does not
end the run: after a minute's rest the tool logs in again and asks only for
what it still lacks, ten times at most.

Needs Python 3.10 or newer and a copy of the whole repository: the titles are
read with the integration's own page parser.
"""

import argparse
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import getpass
from http import HTTPStatus
import http.client
import importlib.util
import ipaddress
import json
import pathlib
import sys
import time
from types import ModuleType
from typing import Any
import urllib.parse

PAGES_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
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

# Restarting a stopped capture by hand, a minute's rest was what the
# controller needed.
RESUME_WAIT_SECONDS = 60.0
MAX_ATTEMPTS = 10
MENU_SEPARATOR = " › "
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
    German controller, what it is, and how it is read.
    """

    key: str
    parent: str
    german: str
    what: str
    menu: bool
    shows_values: bool


STEPS = (
    Step("overview", "", "", "the overview", menu=True, shows_values=False),
    Step(
        "info",
        "overview",
        INFO,
        "the menu of information pages",
        menu=True,
        shows_values=False,
    ),
    Step(
        "heat_pump_menu",
        "overview",
        HEAT_PUMP,
        "the heat pump's main menu",
        menu=True,
        shows_values=False,
    ),
    Step(
        "heat_pump",
        "info",
        HEAT_PUMP,
        "the heat pump's information page",
        menu=False,
        shows_values=True,
    ),
    Step(
        "statistics",
        "info",
        STATISTICS,
        "the statistics",
        menu=False,
        shows_values=True,
    ),
    Step(
        "heating",
        "heat_pump_menu",
        HEATING,
        "the heat pump's heating settings",
        menu=True,
        shows_values=True,
    ),
)
STEP_BY_KEY = {step.key: step for step in STEPS}


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
        try:
            connection.request(method, path, body=form, headers=headers)
            response = connection.getresponse()
            body = response.read(MAX_PAGE_BYTES + 1)
        except TRANSPORT_ERRORS as error:
            raise self._no_answer(f"no answer ({type(error).__name__})") from error
        finally:
            connection.close()
            self._last = time.monotonic()
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


def _pick(step: Step, offered: list[Any], ask: Callable[[str], str]) -> Any:
    """The entry the user names by its number."""
    print(f"\nWhich of these is {step.what}?")
    for number, entry in enumerate(offered, start=1):
        print(f"  {number}. {entry.title}")
    while True:
        answer = ask(f"Number of {step.what} (German: {step.german}): ").strip()
        if answer.isascii() and answer.isdigit() and 1 <= int(answer) <= len(offered):
            return offered[int(answer) - 1]
        print(f"   A number from 1 to {len(offered)}, please.")


def _entry(
    step: Step,
    read: dict[str, list[Any]],
    chosen: dict[str, Any],
    ask: Callable[[str], str],
) -> Any:
    """The entry of its parent page the step reads.

    Found by its German title, else picked by the user - once: a later
    attempt goes the same way.
    """
    if step.key not in chosen:
        offered = [entry for entry in read[step.parent] if entry.href is not None]
        german = [entry for entry in offered if entry.title == step.german]
        chosen[step.key] = german[0] if len(german) == 1 else _pick(step, offered, ask)
    return chosen[step.key]


def _entries(step: Step, text: str, path: str) -> list[Any]:
    """What the page shows: the main menus, the entries one level down, or the values."""
    if not step.parent:
        return [pages.Entry(title, "", href) for title, href in pages.main_menus(text)]
    if step.menu:
        return pages.children(text, path)
    return [pages.Entry(title, shown, None) for title, shown in pages.values(text)]


def _whole(step: Step, entries: list[Any]) -> bool:
    """Something shown, and a value beside every entry where values belong."""
    return bool(entries) and (
        not step.shows_values or all(entry.text for entry in entries)
    )


def _read_missing(
    session: Session,
    read: dict[str, list[Any]],
    chosen: dict[str, Any],
    ask: Callable[[str], str],
) -> None:
    """Read every page not yet read whole, in the order of the menus."""
    for step in STEPS:
        if step.key in read:
            continue
        path = _entry(step, read, chosen, ask).href if step.parent else OVERVIEW
        entries = _entries(step, session.page(path, step.key), path)
        if not _whole(step, entries):
            raise Half(f"{step.what} came half")
        read[step.key] = entries
        print(f"   {step.what}: {len(entries)} entries")


def _report(read: dict[str, list[Any]], chosen: dict[str, Any]) -> dict[str, Any]:
    return {
        "pages": [
            {
                "page": step.key,
                "german": _path(step, lambda each: each.german),
                "shown": _path(step, lambda each: chosen[each.key].title),
                "entries": [[entry.title, entry.text] for entry in read[step.key]],
            }
            for step in STEPS
        ]
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
    """The six pages' titles and values, or None when they could not be had."""
    read: dict[str, list[Any]] = {}
    chosen: dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        session = Session(host, user, password, sleep)
        try:
            session.login()
            _read_missing(session, read, chosen, ask)
        except Refused as error:
            print(f"Login refused: {error}.")
            return None
        except Stopped as error:
            print(f"Attempt {attempt} of {attempts} stopped: {error}.")
        else:
            return _report(read, chosen)
        finally:
            session.logout()
        if attempt < attempts:
            print(f"   Going on in {RESUME_WAIT_SECONDS:.0f} s.")
            sleep(RESUME_WAIT_SECONDS)
    print(f"Gave up after {attempts} attempts; try again later.")
    return None


def main(argv: list[str] | None = None) -> int:
    """Ask for the credentials, read the pages, and save the file."""
    # A console that cannot show a title must not end the run.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("host", help="the heat pump's address, as in the integration")
    arguments = parser.parse_args(argv)
    user = input("Web interface user: ").strip()
    password = getpass.getpass("Web interface password: ")
    report = capture(arguments.host, user, password, input)
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
