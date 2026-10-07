"""The title capture for another language: six pages, read only, nothing personal.

tools/webif_capture.py is handed to people whose controller has to stay on
a language the integration cannot read yet. It runs against a pump nobody
here has seen, and the file it writes goes to a public issue - so what it
asks the pump for and what it writes down are held by these tests, against
the stand-in on the loopback address.
"""

import asyncio
import http.client
import importlib.util
import json
import pathlib
import subprocess
import sys
import threading
import time

import pytest

from custom_components.weishaupt_modbus.webif import client as webif, discovery

from .webif_stand_in import (
    HEAT_PUMP_INFO,
    HEATING,
    INFO,
    PASSWORD,
    PUMP_MENU,
    SESSION_ID,
    STACK,
    USER,
    StandInPump,
    column,
    link,
    see_other,
    serving,
    value,
)

REPO = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "webif_capture", REPO / "tools" / "webif_capture.py"
)
capture_tool = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = capture_tool
_SPEC.loader.exec_module(capture_tool)

# The six pages' segments on the controller the tool knows, with the
# stand-in's device token (0A0B): their codes find them in any language.
KNOWN = {
    "info": INFO,
    "pump": PUMP_MENU,
    "heat_pump": HEAT_PUMP_INFO,
    "statistics": "0C000C28000000000000000A0B020003000401",
    "heating": HEATING,
}
# Another model's: no code the tool knows, so the titles or the user decide.
OTHER_MODEL = {
    "info": "0D000001000000000080000A0B010002000301",
    "pump": "6A000001000000000080000A0B010002000301",
    "heat_pump": "0D000D22000000000000000A0B020003000401",
    "statistics": "0D000D28000000000000000A0B020003000401",
    "heating": "6A001800000000000080000A0B020003000401",
}
SERVICE = "64001100000000000080000A0B020003000401"
RESET = "64004500000000000080000A0B020011000401"
COOLING = "64001900000000000080000A0B020003000401"
UNKNOWN_HEATING = "64009900000000000080000A0B020003000401"
SWITCHING_DIFFERENCE = "64001805000000002D40000A0B030011010401"
POWER_LIMIT = "64001807000000003C40000A0B030011010401"


def paths(codes):
    """The six pages' addresses, in the order the tool reads them."""
    return [
        webif.OVERVIEW,
        STACK + codes["info"],
        STACK + codes["pump"],
        STACK + f"{codes['info']},{codes['heat_pump']}",
        STACK + f"{codes['info']},{codes['statistics']}",
        STACK + f"{codes['pump']},{codes['heating']}",
    ]


WALK = paths(KNOWN)
HEAT_PUMP_PAGE, STATISTICS_PAGE = WALK[3], WALK[4]
LOGIN = [("GET", webif.INDEX), ("POST", webif.LOGIN)]
LOGOUT = [("GET", webif.LOGOUT)]
A_MINUTE = 60
# The shell's code for a run ended by Ctrl+C.
STOPPED_BY_THE_USER = 130
GERMAN = {
    "info": "Info",
    "pump": "Wärmepumpe",
    "heat_pump": "Wärmepumpe",
    "statistics": "Statistik",
    "heating": "Heizen",
}
ENGLISH = {
    "info": "Information",
    "pump": "Heat pump",
    "heat_pump": "Heat pump",
    "statistics": "Statistics",
    "heating": "Heating",
}


def asked(paths):
    return [("GET", path) for path in paths]


def site(titles, codes=KNOWN):
    """The six pages, with the menu titles of one language and the codes of
    one model; the heat pump menu also lists Service and Reset."""
    overview, info, pump, heat_pump, statistics, heating = paths(codes)
    main = column(
        link([codes["info"]], titles["info"]) + link([codes["pump"]], titles["pump"])
    )
    return {
        overview: main,
        info: main
        + column(
            link([codes["info"], codes["heat_pump"]], titles["heat_pump"])
            + link([codes["info"], codes["statistics"]], titles["statistics"])
        ),
        pump: main
        + column(
            link([codes["pump"], codes["heating"]], titles["heating"])
            + link([codes["pump"], SERVICE], "Service")
            + link([codes["pump"], RESET], "Reset")
        ),
        heat_pump: column(
            value("Flow temperature", "35.2 °C") + value("Compressor", "Off")
        ),
        statistics: column(value("Heat quantity heating", "5356.310 KWh")),
        heating: column(
            link(
                [codes["pump"], codes["heating"], SWITCHING_DIFFERENCE],
                "Switching difference",
                "4.5 K",
            )
            + link(
                [codes["pump"], codes["heating"], POWER_LIMIT], "Power limit", "60 %"
            )
        ),
    }


async def run_capture(pump, answers=(), password=PASSWORD, **options):
    """The capture against the stand-in: its report, the questions, the rests."""
    questions, rests = [], []
    replies = list(answers)

    def ask(question):
        questions.append(question)
        assert replies, f"asked more than expected: {question}"
        return replies.pop(0)

    report = await asyncio.to_thread(
        capture_tool.capture,
        pump.host,
        USER,
        password,
        ask,
        sleep=rests.append,
        **options,
    )
    return report, questions, rests


def by_page(report):
    return {page["page"]: page for page in report["pages"]}


async def test_a_german_controller_is_read_without_a_question(socket_enabled):
    async with serving(StandInPump()) as pump:
        pump.site = site(GERMAN)
        # A slow answer: the gap counts from its end, not from the request.
        pump.delays[HEAT_PUMP_PAGE] = 0.5
        report, questions, rests = await run_capture(pump)

    assert questions == []
    # Six pages and nothing else: the heat pump menu's Reset stays unopened.
    assert pump.asked == [*LOGIN, *asked(WALK), *LOGOUT]
    assert [page["shown"] for page in report["pages"]] == [
        "",
        "Info",
        "Wärmepumpe",
        "Info › Wärmepumpe",
        "Info › Statistik",
        "Wärmepumpe › Heizen",
    ]
    assert len(rests) == len(pump.asked) - 1
    assert all(
        webif.MIN_GAP_SECONDS - 0.1 <= rest <= webif.MIN_GAP_SECONDS for rest in rests
    ), rests


async def test_known_codes_find_the_pages_in_any_language(socket_enabled):
    """A pick is where a mistyped number opens a wrong page: the codes of the
    controller this was built for leave nothing to pick."""
    async with serving(StandInPump()) as pump:
        pump.site = site(ENGLISH)
        report, questions, _ = await run_capture(pump)

    assert questions == []
    assert pump.asked == [*LOGIN, *asked(WALK), *LOGOUT]
    assert by_page(report)["heating"]["shown"] == "Heat pump › Heating"


async def test_another_model_in_german_is_read_by_its_titles(socket_enabled):
    async with serving(StandInPump()) as pump:
        pump.site = site(GERMAN, OTHER_MODEL)
        _, questions, _ = await run_capture(pump)

    assert questions == []
    assert pump.asked == [*LOGIN, *asked(paths(OTHER_MODEL)), *LOGOUT]


async def test_another_model_in_another_language_is_picked_by_number(socket_enabled):
    async with serving(StandInPump()) as pump:
        pump.site = site(ENGLISH, OTHER_MODEL)
        report, questions, _ = await run_capture(
            pump,
            answers=["0", "two", "1", "y", "2", "y", "1", "y", "2", "y", "1", "y"],
        )

    assert pump.asked == [*LOGIN, *asked(paths(OTHER_MODEL)), *LOGOUT]
    # Each number question names what it asks for on a German controller, each
    # pick is confirmed by its title; the first two answers were no number of
    # the list and were asked again.
    expected = [
        "German: Info",
        "German: Info",
        "German: Info",
        "'Information'",
        "German: Wärmepumpe",
        "'Heat pump'",
        "German: Wärmepumpe",
        "'Heat pump'",
        "German: Statistik",
        "'Statistics'",
        "German: Heizen",
        "'Heating'",
    ]
    assert len(questions) == len(expected)
    for question, text in zip(questions, expected, strict=True):
        assert text in question, question
    pages = by_page(report)
    assert {key: (page["german"], page["shown"]) for key, page in pages.items()} == {
        "overview": ("", ""),
        "info": ("Info", "Information"),
        "heat_pump_menu": ("Wärmepumpe", "Heat pump"),
        "heat_pump": ("Info › Wärmepumpe", "Information › Heat pump"),
        "statistics": ("Info › Statistik", "Information › Statistics"),
        "heating": ("Wärmepumpe › Heizen", "Heat pump › Heating"),
    }
    assert pages["overview"]["titles"] == ["Information", "Heat pump"]
    assert pages["heat_pump_menu"]["titles"] == ["Heating", "Service", "Reset"]
    assert pages["heat_pump"]["entries"] == [
        ["Flow temperature", "35.2 °C"],
        ["Compressor", "Off"],
    ]
    assert pages["heating"]["entries"] == [
        ["Switching difference", "4.5 K"],
        ["Power limit", "60 %"],
    ]


async def test_reset_and_service_are_never_offered(socket_enabled, capsys):
    """Their codes are known: whatever the language, no number opens them."""
    codes = {**KNOWN, "heating": UNKNOWN_HEATING}
    async with serving(StandInPump()) as pump:
        pump.site = site(ENGLISH, codes)
        _, questions, _ = await run_capture(pump, answers=["2", "1", "y"])

    offered = [line.strip() for line in capsys.readouterr().out.splitlines()]
    assert "1. Heating" in offered
    assert not any(line.endswith((". Service", ". Reset")) for line in offered)
    assert len(questions) == 3, "2 is no number of the list"
    assert not any(SERVICE in path or RESET in path for _, path in pump.asked)


async def test_a_pick_is_opened_only_once_confirmed(socket_enabled):
    codes = {**KNOWN, "heating": UNKNOWN_HEATING}
    async with serving(StandInPump()) as pump:
        pump.site = site(ENGLISH, codes)
        _, questions, _ = await run_capture(pump, answers=["1", "n", "1", "y"])

    assert len(questions) == 4
    assert pump.asked.count(("GET", paths(codes)[5])) == 1


async def test_a_picked_page_of_the_wrong_shape_is_picked_anew(socket_enabled):
    """A wrong pick came back the same way in every one of the ten attempts,
    ten minutes of asking a page that could never fit."""
    codes = {**KNOWN, "heating": UNKNOWN_HEATING}
    cooling = STACK + f"{KNOWN['pump']},{COOLING}"
    async with serving(StandInPump()) as pump:
        pump.site = site(ENGLISH, codes)
        pump.site[STACK + KNOWN["pump"]] += column(
            link([KNOWN["pump"], COOLING], "Cooling")
        )
        pump.site[cooling] = column("")
        report, questions, rests = await run_capture(pump, answers=["2", "y", "1", "y"])

    assert len(questions) == 4
    assert rests.count(A_MINUTE) == 1
    assert pump.asked.count(("GET", cooling)) == 1
    assert by_page(report)["heating"]["entries"] == [
        ["Switching difference", "4.5 K"],
        ["Power limit", "60 %"],
    ]


@pytest.mark.parametrize(
    "half",
    [
        pytest.param(column(""), id="empty column"),
        pytest.param(
            column(value("Flow temperature", "") + value("Compressor", "Off")),
            id="blank value",
        ),
    ],
)
async def test_a_page_served_half_is_asked_for_again_after_a_minute(
    socket_enabled, half
):
    async with serving(StandInPump()) as pump:
        pump.site = site(GERMAN)
        whole = pump.site.pop(HEAT_PUMP_PAGE)
        pump.pages = [half, whole]
        report, _, rests = await run_capture(pump)

    # A new login, and only what was still missing.
    assert pump.asked == [
        *LOGIN,
        *asked(WALK[:4]),
        *LOGOUT,
        *LOGIN,
        *asked(WALK[3:]),
        *LOGOUT,
    ]
    assert rests.count(A_MINUTE) == 1
    assert by_page(report)["heat_pump"]["entries"] == [
        ["Flow temperature", "35.2 °C"],
        ["Compressor", "Off"],
    ]


async def test_a_page_showing_another_sections_values_is_read_again(socket_enabled):
    """The controller now and then sends the statistics in place of the heat
    pump page: whole by every check of its own, and the file taught the
    statistics' titles as the heat pump page's."""
    async with serving(StandInPump()) as pump:
        pump.site = site(GERMAN)
        whole = pump.site.pop(HEAT_PUMP_PAGE)
        pump.pages = [pump.site[STATISTICS_PAGE], whole]
        report, _, rests = await run_capture(pump)

    # Which of the two came right cannot be told: both are asked again.
    assert pump.asked == [
        *LOGIN,
        *asked(WALK[:5]),
        *LOGOUT,
        *LOGIN,
        *asked(WALK[3:]),
        *LOGOUT,
    ]
    assert rests.count(A_MINUTE) == 1
    assert by_page(report)["heat_pump"]["entries"] == [
        ["Flow temperature", "35.2 °C"],
        ["Compressor", "Off"],
    ]


async def test_no_answer_ends_the_attempt_without_a_logout(socket_enabled):
    """After no answer nothing more is asked, not even the logout, and the run
    gives up after its last attempt instead of asking on and on."""
    async with serving(StandInPump()) as pump:
        pump.site = site(GERMAN)
        pump.failing[STATISTICS_PAGE] = 503
        report, _, rests = await run_capture(pump, attempts=2)

    assert report is None
    assert pump.asked == [*LOGIN, *asked(WALK[:5]), *LOGIN, *asked([STATISTICS_PAGE])]
    assert rests.count(A_MINUTE) == 1


SHORT_LIMIT = 0.5
MACHINE_PAUSE = 0.6


class StalledAtTheLogin(StandInPump):
    """The machine stands still while the stand-in serves the login page."""

    async def index(self, request):
        time.sleep(MACHINE_PAUSE)  # noqa: ASYNC251 - the machine standing still
        return await super().index(request)


def short_limit_for(monkeypatch, path):
    """The short time limit for the request meant to run into it, and no other.

    Every request opens a connection of its own, inside its limit: under one
    short limit for all, a pause of the machine timed the login out instead.
    """
    request = capture_tool.Session._request

    def limited(session, method, asked_path, form=None):
        if asked_path != path:
            return request(session, method, asked_path, form)
        with monkeypatch.context() as patch:
            patch.setattr(capture_tool, "TIMEOUT_SECONDS", SHORT_LIMIT)
            return request(session, method, asked_path, form)

    monkeypatch.setattr(capture_tool.Session, "_request", limited)


@pytest.mark.parametrize("stand_in", [StandInPump, StalledAtTheLogin])
async def test_a_page_that_trickles_is_cut_off_at_the_time_limit(
    socket_enabled, monkeypatch, stand_in
):
    """The socket's time limit bounds each read, not the request: a server
    sending a byte now and then kept one request open far past it."""
    short_limit_for(monkeypatch, HEAT_PUMP_PAGE)
    async with serving(stand_in()) as pump:
        pump.site = site(GERMAN)
        pump.trickles[HEAT_PUMP_PAGE] = 0.05
        started = time.monotonic()
        report, _, _ = await run_capture(pump, attempts=1)
        took = time.monotonic() - started

    assert report is None
    # No answer: nothing more is asked, not even the logout. A pause inside
    # its own limit may keep the cut request from the stand-in altogether.
    walked = [*LOGIN, *asked(WALK[:3])]
    assert pump.asked in (walked, [*walked, *asked([HEAT_PUMP_PAGE])])
    assert took < 3, took


async def test_a_deadline_before_the_socket_is_held_still_cuts_the_request(
    socket_enabled, monkeypatch
):
    """A machine standing still between connecting and holding the socket let
    the deadline fire on nothing: the trickle ran on, 13 s past a 0.5 s limit."""
    short_limit_for(monkeypatch, HEAT_PUMP_PAGE)
    fired = threading.Event()
    cut = capture_tool._cut

    def noted(sockets, event):
        cut(sockets, event)
        fired.set()

    monkeypatch.setattr(capture_tool, "_cut", noted)
    connect = http.client.HTTPConnection.connect

    def connect_then_stand_still(connection):
        connect(connection)
        if capture_tool.TIMEOUT_SECONDS == SHORT_LIMIT:
            assert fired.wait(5)

    monkeypatch.setattr(http.client.HTTPConnection, "connect", connect_then_stand_still)
    async with serving(StandInPump()) as pump:
        pump.site = site(GERMAN)
        pump.trickles[HEAT_PUMP_PAGE] = 0.05
        started = time.monotonic()
        report, _, _ = await run_capture(pump, attempts=1)
        took = time.monotonic() - started

    assert report is None
    assert took < 3, took


@pytest.mark.parametrize("answer", ["no database", "no session"])
async def test_a_login_the_controller_cannot_serve_is_tried_again(
    socket_enabled, answer
):
    """Its database out of reach (#nocon) or no session given: the controller
    struggles, and the credentials may well be right."""
    async with serving(StandInPump()) as pump:
        pump.site = site(GERMAN)
        if answer == "no database":
            pump.login_answer = lambda: see_other("/index.html#nocon")
        else:
            pump.sets_cookie = False
        report, _, rests = await run_capture(pump, attempts=2)

    assert report is None
    assert pump.asked == [*LOGIN, *LOGIN]
    assert rests.count(A_MINUTE) == 1


async def test_a_wrong_password_redirected_with_the_address_is_refused(
    socket_enabled,
):
    async with serving(StandInPump()) as pump:
        pump.site = site(GERMAN)
        host = pump.host
        pump.login_answer = lambda: see_other(f"http://{host}/index.html#wrongpassword")
        report, _, rests = await run_capture(pump, attempts=3)

    assert report is None
    assert pump.asked == LOGIN
    assert A_MINUTE not in rests


async def test_an_error_status_on_the_login_page_sends_no_login(socket_enabled):
    async with serving(StandInPump()) as pump:
        pump.site = site(GERMAN)
        pump.failing[webif.INDEX] = 503
        report, _, _ = await run_capture(pump, attempts=1)

    assert report is None
    assert pump.asked == [("GET", webif.INDEX)]


async def test_an_answer_larger_than_any_page_is_no_answer(socket_enabled):
    async with serving(StandInPump()) as pump:
        pump.site = site(GERMAN)
        pump.site[webif.OVERVIEW] = "x" * (webif.MAX_PAGE_BYTES + 10)
        report, _, _ = await run_capture(pump, attempts=1)

    assert report is None
    # No answer: nothing more is asked, not even the logout.
    assert pump.asked == [*LOGIN, *asked(WALK[:1])]


async def test_the_session_refuses_an_address_off_the_positive_list(socket_enabled):
    """The parser lets no such address through today; the request still
    refuses one where it goes out, as the client does."""
    async with serving(StandInPump()) as pump:
        session = capture_tool.Session(pump.host, USER, PASSWORD, lambda _rest: None)
        with pytest.raises(ValueError, match="positive list"):
            await asyncio.to_thread(
                session._request, "GET", webif.OVERVIEW + "?access_code=1234"
            )

    assert pump.asked == []


async def test_picks_are_kept_for_a_later_attempt(socket_enabled):
    """A page that got no answer is asked for again the way it was picked."""
    async with serving(StandInPump()) as pump:
        pump.site = site(ENGLISH, OTHER_MODEL)
        pump.failing_once[paths(OTHER_MODEL)[3]] = 503
        report, questions, rests = await run_capture(
            pump, answers=["1", "y", "2", "y", "1", "y", "2", "y", "1", "y"]
        )

    assert report is not None
    assert rests.count(A_MINUTE) == 1
    assert len(questions) == 10, "every pick asked once"


async def test_refused_credentials_are_not_tried_again(socket_enabled):
    async with serving(StandInPump()) as pump:
        pump.site = site(GERMAN)
        report, _, rests = await run_capture(pump, password="wrong")

    assert report is None
    assert pump.asked == LOGIN
    assert A_MINUTE not in rests


async def test_the_report_holds_nothing_that_points_to_the_pump(socket_enabled):
    async with serving(StandInPump()) as pump:
        pump.site = site(GERMAN)
        report, _, _ = await run_capture(pump)
        host = pump.host

    text = json.dumps(report, ensure_ascii=False)
    # The address, the credentials, the session, and the page addresses,
    # whose segments carry a device token (0A0B in the stand-in).
    for personal in (
        host,
        "127.0.0.1",
        USER,
        PASSWORD,
        SESSION_ID[:8],
        "stack=",
        "0A0B",
    ):
        assert personal not in text, personal


async def test_a_menu_keeps_its_titles_only(socket_enabled):
    """Only the three pages the integration reads need their values, for the
    units; what a menu shows beside an entry went into a file meant for a
    public issue all the same."""
    async with serving(StandInPump()) as pump:
        pump.site = site(GERMAN)
        info = STACK + KNOWN["info"]
        pump.site[info] = pump.site[info].replace(
            ">Statistik</h5>\n", ">Statistik</h5>\n192.0.2.10"
        )
        report, _, _ = await run_capture(pump)

    assert "192.0.2.10" not in json.dumps(report)
    assert by_page(report)["info"]["titles"] == ["Wärmepumpe", "Statistik"]
    assert "entries" not in by_page(report)["info"]


@pytest.mark.parametrize(
    "name",
    [
        "INDEX",
        "LOGIN",
        "LOGOUT",
        "OVERVIEW",
        "LOGIN_TARGET",
        "WRONG_PASSWORD",
        "SESSION_COOKIE",
        "LOGIN_USER_FIELD",
        "LOGIN_PASSWORD_FIELD",
        "TIMEOUT_SECONDS",
        "MIN_GAP_SECONDS",
        "MAX_PAGE_BYTES",
    ],
)
def test_the_tool_talks_like_the_client(name):
    """The tool cannot import the client, which needs aiohttp; its copies of
    the protocol must not drift from the client's."""
    assert getattr(capture_tool, name) == getattr(webif, name)


@pytest.mark.parametrize("name", ["INFO", "HEAT_PUMP", "STATISTICS", "HEATING"])
def test_the_tool_looks_for_the_titles_the_integration_finds(name):
    assert getattr(capture_tool, name) == getattr(discovery, name)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", webif.INDEX),
        ("POST", webif.LOGIN),
        ("GET", webif.LOGOUT),
        ("GET", webif.OVERVIEW),
        ("GET", STACK + INFO),
        ("GET", webif.OVERVIEW + "?access_code=1234"),
        ("GET", STACK + INFO + "&access_code=1234"),
        ("POST", "/pro_save.html"),
        ("GET", "/pro_save.html"),
        ("POST", webif.INDEX),
        ("PUT", webif.OVERVIEW),
    ],
)
def test_the_tool_asks_only_what_the_client_may(method, path):
    assert capture_tool._allowed(method, path) == webif._allowed(method, path)


def test_a_web_address_is_refused_before_anything_is_asked(monkeypatch):
    """Given as a URL, the address ended in a traceback - after the user had
    typed the user name and the password."""
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("asked"))
    monkeypatch.setattr(capture_tool, "capture", lambda *_arguments: pytest.fail("ran"))

    with pytest.raises(SystemExit):
        capture_tool.main(["http://192.0.2.1/"])


def test_ctrl_c_ends_the_run_without_a_traceback(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: USER)
    monkeypatch.setattr(capture_tool.getpass, "getpass", lambda _prompt: PASSWORD)

    def interrupted(*_arguments):
        raise KeyboardInterrupt

    monkeypatch.setattr(capture_tool, "capture", interrupted)

    assert capture_tool.main(["192.0.2.1"]) == STOPPED_BY_THE_USER


def test_the_tool_alone_says_it_needs_the_repository(tmp_path):
    copy = tmp_path / "webif_capture.py"
    copy.write_bytes((REPO / "tools" / "webif_capture.py").read_bytes())

    result = subprocess.run(
        [sys.executable, str(copy), "192.0.2.1"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 1
    assert "whole repository" in result.stderr
    assert "Traceback" not in result.stderr


def test_the_file_lands_where_the_tool_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _prompt: USER)
    monkeypatch.setattr(capture_tool.getpass, "getpass", lambda _prompt: PASSWORD)
    report = {"pages": [{"page": "statistics", "entries": [["Wärmemenge", "5 KWh"]]}]}
    calls = []

    def capture(*arguments):
        calls.append(arguments[:3])
        return report

    monkeypatch.setattr(capture_tool, "capture", capture)

    assert capture_tool.main(["192.0.2.1"]) == 0
    assert calls == [("192.0.2.1", USER, PASSWORD)]
    [written] = tmp_path.glob("*.json")
    assert json.loads(written.read_text(encoding="utf-8")) == report

    monkeypatch.setattr(capture_tool, "capture", lambda *_arguments: None)
    assert capture_tool.main(["192.0.2.1"]) == 1
    assert list(tmp_path.glob("*.json")) == [written]
