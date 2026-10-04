"""The title capture for another language: six pages, read only, nothing personal.

tools/webif_capture.py is handed to people whose controller has to stay on
a language the integration cannot read yet. It runs against a pump nobody
here has seen, and the file it writes goes to a public issue - so what it
asks the pump for and what it writes down are held by these tests, against
the stand-in on the loopback address.
"""

import asyncio
import importlib.util
import json
import pathlib
import sys

import pytest

from custom_components.weishaupt_modbus.webif import client as webif, discovery

from .webif_stand_in import (
    HEAT_PUMP_INFO,
    HEATING,
    INFO,
    PASSWORD,
    PUMP_MENU,
    RESET,
    SESSION_ID,
    STACK,
    STATISTICS_INFO,
    USER,
    StandInPump,
    column,
    link,
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

HEAT_PUMP_PAGE = STACK + f"{INFO},{HEAT_PUMP_INFO}"
STATISTICS_PAGE = STACK + f"{INFO},{STATISTICS_INFO}"
HEATING_PAGE = STACK + f"{PUMP_MENU},{HEATING}"
SWITCHING_DIFFERENCE = "64001805000000002D40000A0B030011010401"
POWER_LIMIT = "64001807000000003C40000A0B030011010401"
WALK = [
    webif.OVERVIEW,
    STACK + INFO,
    STACK + PUMP_MENU,
    HEAT_PUMP_PAGE,
    STATISTICS_PAGE,
    HEATING_PAGE,
]
LOGIN = [("GET", webif.INDEX), ("POST", webif.LOGIN)]
LOGOUT = [("GET", webif.LOGOUT)]
A_MINUTE = 60
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


def site(titles):
    """The six pages, with the menu titles of one language."""
    main = column(link([INFO], titles["info"]) + link([PUMP_MENU], titles["pump"]))
    return {
        webif.OVERVIEW: main,
        STACK + INFO: main
        + column(
            link([INFO, HEAT_PUMP_INFO], titles["heat_pump"])
            + link([INFO, STATISTICS_INFO], titles["statistics"])
        ),
        STACK + PUMP_MENU: main
        + column(
            link([PUMP_MENU, HEATING], titles["heating"])
            + link([PUMP_MENU, RESET], "Reset")
        ),
        HEAT_PUMP_PAGE: column(
            value("Flow temperature", "35.2 °C") + value("Compressor", "Off")
        ),
        STATISTICS_PAGE: column(value("Heat quantity heating", "5356.310 KWh")),
        HEATING_PAGE: column(
            link(
                [PUMP_MENU, HEATING, SWITCHING_DIFFERENCE],
                "Switching difference",
                "4.5 K",
            )
            + link([PUMP_MENU, HEATING, POWER_LIMIT], "Power limit", "60 %")
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
    assert all(0 < rest <= webif.MIN_GAP_SECONDS for rest in rests), rests


async def test_another_language_is_picked_by_number(socket_enabled):
    async with serving(StandInPump()) as pump:
        pump.site = site(ENGLISH)
        report, questions, _ = await run_capture(
            pump, answers=["0", "two", "1", "2", "1", "2", "1"]
        )

    assert pump.asked == [*LOGIN, *asked(WALK), *LOGOUT]
    # Each question names what it asks for on a German controller; the first
    # two answers were no number of the list and were asked again.
    german = ["Info", "Info", "Info", "Wärmepumpe", "Wärmepumpe", "Statistik", "Heizen"]
    assert len(questions) == len(german)
    for question, title in zip(questions, german, strict=True):
        assert f"German: {title}" in question, question
    pages = by_page(report)
    assert {key: (page["german"], page["shown"]) for key, page in pages.items()} == {
        "overview": ("", ""),
        "info": ("Info", "Information"),
        "heat_pump_menu": ("Wärmepumpe", "Heat pump"),
        "heat_pump": ("Info › Wärmepumpe", "Information › Heat pump"),
        "statistics": ("Info › Statistik", "Information › Statistics"),
        "heating": ("Wärmepumpe › Heizen", "Heat pump › Heating"),
    }
    assert pages["overview"]["entries"] == [["Information", ""], ["Heat pump", ""]]
    assert pages["heat_pump_menu"]["entries"] == [["Heating", ""], ["Reset", ""]]
    assert pages["heat_pump"]["entries"] == [
        ["Flow temperature", "35.2 °C"],
        ["Compressor", "Off"],
    ]
    assert pages["heating"]["entries"] == [
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
