"""The title capture for another language or model: read only, nothing personal.

tools/webif_capture.py is handed to people whose controller has to stay on
a language the integration cannot read yet, or is a model it does not know
yet. It runs against a pump nobody
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

from custom_components.weishaupt_modbus import webif_sensor
from custom_components.weishaupt_modbus.webif import client as webif, discovery, setting

from .webif_stand_in import (
    HEAT_PUMP_INFO,
    HEATING,
    INFO,
    MODULATION,
    PASSWORD,
    PUMP_MENU,
    SESSION_ID,
    STACK,
    USER,
    StandInPump,
    column,
    limit_leaf,
    link,
    modulation_form,
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


# The pages only some pumps show, as an Evoblock showed them in German, at
# made-up segments shaped like the controller's.
CIRCUIT_INFO = {
    2: "0C000C32000000000000000A0B020003000401",
    3: "0C000C33000000000000000A0B020003000401",
}
SECOND_GENERATOR_INFO = "0C000C40000000000000000A0B020003000401"
CIRCUIT_MENU = {
    2: "33000001000000000080000A0B010002000301",
    3: "34000001000000000080000A0B010002000301",
}
HOT_WATER_MENU = "46000001000000000080000A0B010002000301"
MODULATION_OFFERED = [-1, *range(50, 101, 5)]


def settings_segment(menu):
    return menu[:2] + "00320000000000008000" + "0A0B020003000401"


def menu_reset(menu):
    return menu[:2] + "00450000000000008000" + "0A0B020003000401"


def setting_link(menu, title, shown):
    segment = menu[:2] + "003200" + "0000000000" + "40000A0B030011010401"
    return link([menu, settings_segment(menu), segment], title, shown)


def settings_path(menu):
    return STACK + f"{menu},{settings_segment(menu)}"


OPTIONAL_WALK = [
    STACK + f"{INFO},{CIRCUIT_INFO[2]}",
    STACK + f"{INFO},{SECOND_GENERATOR_INFO}",
    STACK + f"{KNOWN['pump']},{MODULATION}",
    STACK + CIRCUIT_MENU[2],
    settings_path(CIRCUIT_MENU[2]),
    STACK + HOT_WATER_MENU,
    settings_path(HOT_WATER_MENU),
]
ALL_ABSENT = [
    "Info › Heizkreis n",
    "Info › 2. WEZ",
    "Wärmepumpe › Modulation",
    "Heizkreis n",
    "Heizkreis n › Einstellungen",
    "Warmwasser",
    "Warmwasser › Einstellungen",
]


def with_optional_pages(pages, titles=None):
    """The six pages, and those only some pumps show, in German.

    Info lists the third circuit before the second: the lowest is read. Each
    circuit's and the hot water's menu also lists Reset.
    """
    titles = titles or {
        "circuit": "Heizkreis",
        "second_generator": "2. WEZ",
        "modulation": "Modulation",
        "hot_water": "Warmwasser",
        "settings": "Einstellungen",
    }
    circuit = titles["circuit"]
    overview, info, pump = paths(KNOWN)[:3]
    main = column(
        link([CIRCUIT_MENU[2]], f"{circuit} 2", "FBH")
        + link([CIRCUIT_MENU[3]], f"{circuit} 3", "RADIATOREN")
        + link([HOT_WATER_MENU], titles["hot_water"])
    )
    pages = dict(pages)
    pages[overview] += main
    pages[info] += main + column(
        link([INFO, CIRCUIT_INFO[3]], f"{circuit} 3")
        + link([INFO, CIRCUIT_INFO[2]], f"{circuit} 2")
        + link([INFO, SECOND_GENERATOR_INFO], titles["second_generator"])
    )
    pages[pump] += main + column(
        link([KNOWN["pump"], MODULATION], titles["modulation"])
    )
    for menu in (CIRCUIT_MENU[2], CIRCUIT_MENU[3], HOT_WATER_MENU):
        pages[STACK + menu] = column(
            link([menu, settings_segment(menu)], titles["settings"])
            + link([menu, menu_reset(menu)], "Reset")
        )
    pages[STACK + f"{INFO},{CIRCUIT_INFO[2]}"] = column(
        value("Außentemperatur gemittelt", "5.0 °C")
    )
    # A row without a value: whether every row shows one nobody here has seen.
    pages[STACK + f"{INFO},{SECOND_GENERATOR_INFO}"] = column(
        value("Betriebsstunden", "120 h") + value("Störung", "")
    )
    pages[STACK + f"{KNOWN['pump']},{MODULATION}"] = modulation_form(80)
    pages[settings_path(CIRCUIT_MENU[2])] = column(
        setting_link(CIRCUIT_MENU[2], "Minimale Vorlauftemperatur", "20.0 °C")
    )
    pages[settings_path(HOT_WATER_MENU)] = column(
        setting_link(HOT_WATER_MENU, "Betriebsart", "Automatik")
    )
    return pages


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


async def test_the_pages_only_some_pumps_show_are_read_where_shown(socket_enabled):
    """The circuits, the second heat generator, both settings pages and the
    modulation: what the integration reads on some pumps, by their German
    titles. The menus' Reset stays shut."""
    async with serving(StandInPump()) as pump:
        pump.site = with_optional_pages(site(GERMAN))
        report, questions, _ = await run_capture(pump)

    assert questions == []
    assert pump.asked == [*LOGIN, *asked(WALK + OPTIONAL_WALK), *LOGOUT]
    assert report["absent"] == []
    pages = by_page(report)
    assert {key: pages[key]["shown"] for key in list(pages)[6:]} == {
        "heating_circuit": "Info › Heizkreis 2",
        "second_generator": "Info › 2. WEZ",
        "modulation": "Wärmepumpe › Modulation",
        "circuit_menu": "Heizkreis 2",
        "circuit_settings": "Heizkreis 2 › Einstellungen",
        "hot_water_menu": "Warmwasser",
        "hot_water_settings": "Warmwasser › Einstellungen",
    }
    assert pages["heating_circuit"]["entries"] == [
        ["Außentemperatur gemittelt", "5.0 °C"]
    ]
    assert pages["second_generator"]["entries"] == [
        ["Betriebsstunden", "120 h"],
        ["Störung", ""],
    ]
    assert pages["circuit_settings"]["entries"] == [
        ["Minimale Vorlauftemperatur", "20.0 °C"]
    ]
    # A word is kept as text alone: it could be a name the user gave.
    assert pages["hot_water_settings"]["entries"] == [
        ["Betriebsart", capture_tool.TEXT_SHOWN]
    ]
    # What the form offers; the value set is the user's, not a title.
    assert pages["modulation"] == {
        "page": "modulation",
        "german": "Wärmepumpe › Modulation",
        "shown": "Wärmepumpe › Modulation",
        "offered": MODULATION_OFFERED,
    }
    assert pages["circuit_menu"]["titles"] == ["Einstellungen", "Reset"]


async def test_the_pages_a_pump_does_not_show_are_named_absent(socket_enabled):
    async with serving(StandInPump()) as pump:
        pump.site = site(GERMAN)
        report, questions, _ = await run_capture(pump)

    assert questions == []
    assert pump.asked == [*LOGIN, *asked(WALK), *LOGOUT]
    assert len(report["pages"]) == 6
    assert report["absent"] == ALL_ABSENT


async def test_the_pages_only_some_pumps_show_are_never_offered_to_pick(
    socket_enabled,
):
    """Their menus list Reset under codes nobody here knows: a pick is where
    a mistyped number would open it."""
    english = {
        "circuit": "Heating circuit",
        "second_generator": "2nd heat generator",
        "modulation": "Modulation output",
        "hot_water": "Hot water",
        "settings": "Settings",
    }
    async with serving(StandInPump()) as pump:
        pump.site = with_optional_pages(site(GERMAN), english)
        report, questions, _ = await run_capture(pump)

    assert questions == []
    assert pump.asked == [*LOGIN, *asked(WALK), *LOGOUT]
    assert report["absent"] == ALL_ABSENT


async def test_a_name_given_to_a_circuit_stays_out_of_the_file(socket_enabled):
    """The file goes to a public issue; what a settings page shows as text,
    a circuit's name among it, is the user's."""
    async with serving(StandInPump()) as pump:
        pump.site = with_optional_pages(site(GERMAN))
        pump.site[settings_path(CIRCUIT_MENU[2])] += column(
            setting_link(CIRCUIT_MENU[2], "Name", "Kinderzimmer Max")
        )
        report, _, _ = await run_capture(pump)

    assert "Kinderzimmer Max" not in json.dumps(report, ensure_ascii=False)
    assert ["Name", capture_tool.TEXT_SHOWN] in by_page(report)["circuit_settings"][
        "entries"
    ]
    assert ["Minimale Vorlauftemperatur", "20.0 °C"] in by_page(report)[
        "circuit_settings"
    ]["entries"]


async def test_the_settings_are_the_circuits_info_lists_first(socket_enabled):
    """A circuit switched off shows in the overview but not under Info: its
    settings stood in for the first circuit's."""
    first = CIRCUIT_MENU[2].replace("33", "32", 1)
    async with serving(StandInPump()) as pump:
        pump.site = with_optional_pages(site(GERMAN))
        pump.site[paths(KNOWN)[0]] += column(link([first], "Heizkreis 1"))
        pump.site[STACK + first] = column(
            link([first, settings_segment(first)], "Einstellungen")
        )
        pump.site[settings_path(first)] = column(setting_link(first, "Ein/Aus", "Aus"))
        report, _, _ = await run_capture(pump)

    pages = by_page(report)
    assert pages["circuit_menu"]["shown"] == "Heizkreis 2"
    assert pages["circuit_settings"]["shown"] == "Heizkreis 2 › Einstellungen"
    assert ("GET", STACK + first) not in pump.asked


async def test_the_circuit_menu_is_absent_without_a_circuit_under_info(socket_enabled):
    async with serving(StandInPump()) as pump:
        pump.site = with_optional_pages(site(GERMAN))
        info = paths(KNOWN)[1]
        pump.site[info] = (
            pump.site[info]
            .replace(">Heizkreis 2<", ">Unused 2<")
            .replace(">Heizkreis 3<", ">Unused 3<")
        )
        report, _, _ = await run_capture(pump)

    assert "Heizkreis n" in report["absent"]
    assert ("GET", STACK + CIRCUIT_MENU[2]) not in pump.asked


async def test_an_optional_menu_with_nothing_to_open_is_absent(socket_enabled):
    """The hot water menu listing only its time program: the run gave up
    after its last attempt, the six pages read whole lost with it."""
    async with serving(StandInPump()) as pump:
        pump.site = with_optional_pages(site(GERMAN))
        program = "46004000" + settings_segment(HOT_WATER_MENU)[8:]
        pump.site[STACK + HOT_WATER_MENU] = column(
            link([HOT_WATER_MENU, program], "Zeitprogramm")
        )
        report, _, rests = await run_capture(pump, attempts=2)

    assert report is not None
    assert "Warmwasser › Einstellungen" in report["absent"]
    assert A_MINUTE not in rests


async def test_an_optional_page_that_never_fits_is_absent_after_two_tries(
    socket_enabled,
):
    async with serving(StandInPump()) as pump:
        pump.site = with_optional_pages(site(GERMAN))
        pump.site[OPTIONAL_WALK[1]] = column("")
        report, _, rests = await run_capture(pump, attempts=3)

    assert report is not None
    assert "Info › 2. WEZ" in report["absent"]
    assert rests.count(A_MINUTE) == 1
    assert len(report["pages"]) == 12


async def test_an_optional_page_showing_another_pages_values_is_absent_after_two_tries(
    socket_enabled,
):
    """A circuit's page served as the heat pump's on every try: the run
    gave up after its last attempt, the six pages read whole lost with it."""
    async with serving(StandInPump()) as pump:
        pump.site = with_optional_pages(site(GERMAN))
        pump.site[OPTIONAL_WALK[0]] = pump.site[HEAT_PUMP_PAGE]
        report, _, _ = await run_capture(pump, attempts=3)

    assert report is not None
    assert "Info › Heizkreis n" in report["absent"]
    assert "heat_pump" in by_page(report)
    # Asked once more at the first match, which of the two came wrong being
    # unknown, and kept at the second.
    assert pump.asked.count(("GET", HEAT_PUMP_PAGE)) == 2


async def test_a_wrong_first_answer_of_a_required_page_is_not_kept(socket_enabled):
    """The heat pump page came once with the circuit page's rows; the circuit
    page then came right and was left out, the wrong rows kept as the heat
    pump's."""
    async with serving(StandInPump()) as pump:
        pump.site = with_optional_pages(site(GERMAN))
        right = pump.site.pop(HEAT_PUMP_PAGE)
        pump.pages = [pump.site[OPTIONAL_WALK[0]], right]
        report, _, _ = await run_capture(pump, attempts=3)

    pages = by_page(report)
    assert pages["heat_pump"]["entries"] == [
        ["Flow temperature", "35.2 °C"],
        ["Compressor", "Off"],
    ]
    assert "heating_circuit" in pages


def test_the_units_kept_are_those_the_integration_reads():
    assert {
        webif_sensor.CELSIUS,
        webif_sensor.KELVIN,
        webif_sensor.BAR,
        webif_sensor.KILOWATT,
        webif_sensor.KILOWATT_HOURS,
        webif_sensor.CUBIC_METRES_PER_HOUR,
        webif_sensor.RPM,
        webif_sensor.HERTZ,
        webif_sensor.HOURS,
    } <= set(capture_tool.SHOWN_UNITS)


@pytest.mark.parametrize("shown", ["3.0 KW", "1.1 m3/h", "2400 rpm", "-5.5 °C", "60 %"])
def test_a_number_with_its_unit_is_kept(shown):
    assert capture_tool._neutral("Leistung", shown) == shown


async def test_a_number_given_as_a_circuits_name_stays_out_of_the_file(socket_enabled):
    """A name such as a flat's number passed for a value; a counter showing
    the same number is one."""
    async with serving(StandInPump()) as pump:
        pump.site = with_optional_pages(site(GERMAN))
        pump.site[settings_path(CIRCUIT_MENU[2])] += column(
            setting_link(CIRCUIT_MENU[2], "Name", "007")
            + setting_link(CIRCUIT_MENU[2], "Schaltspiele", "007")
        )
        report, _, _ = await run_capture(pump)

    entries = by_page(report)["circuit_settings"]["entries"]
    assert ["Name", capture_tool.TEXT_SHOWN] in entries
    assert ["Schaltspiele", "007"] in entries


async def test_an_optional_page_misfitting_in_the_last_attempt_is_absent(
    socket_enabled,
):
    """The six pages read whole in the last attempt, an optional one half:
    the run gave up and wrote nothing."""
    async with serving(StandInPump()) as pump:
        pump.site = with_optional_pages(site(GERMAN))
        pump.site[OPTIONAL_WALK[1]] = column("")
        report, _, _ = await run_capture(pump, attempts=1)

    assert report is not None
    assert "Info › 2. WEZ" in report["absent"]


def test_a_pick_offers_no_entry_sharing_its_link():
    """Which page a shared link leads to cannot be told; offered, it was
    picked and its titles taught as the asked page's."""
    shared = (
        STACK
        + "64000001000000000080000A0B010002000301,64009900000000000080000A0B020003000401"
    )
    own = (
        STACK
        + "64000001000000000080000A0B010002000301,64009800000000000080000A0B020003000401"
    )
    walk = capture_tool._Walk()
    walk.read["heat_pump_menu"] = [
        capture_tool.pages.Entry("Heating", "", shared),
        capture_tool.pages.Entry("Cooling", "", shared),
        capture_tool.pages.Entry("Reset", "", own),
    ]
    offered = []

    def ask(question):
        offered.append(question)
        return "1" if "Number" in question else "y"

    entry = capture_tool._entry(capture_tool.STEP_BY_KEY["heating"], walk, ask)

    assert entry.href == own


def test_a_menu_whose_entries_all_share_links_stops_the_attempt():
    shared = (
        STACK
        + "64000001000000000080000A0B010002000301,64009900000000000080000A0B020003000401"
    )
    walk = capture_tool._Walk()
    walk.read["heat_pump_menu"] = [
        capture_tool.pages.Entry("Heating", "", shared),
        capture_tool.pages.Entry("Cooling", "", shared),
    ]

    with pytest.raises(capture_tool.Half):
        capture_tool._entry(
            capture_tool.STEP_BY_KEY["heating"],
            walk,
            lambda _: pytest.fail("nothing clear to pick"),
        )


async def test_a_menu_once_served_with_shared_links_is_asked_for_again(socket_enabled):
    """Kept, the menu stopped every later attempt the same way, and the run
    gave up without asking the controller again."""
    async with serving(StandInPump()) as pump:
        pump.site = site(GERMAN)
        whole = pump.site.pop(WALK[2])
        shared = column(
            link([KNOWN["pump"], KNOWN["heating"]], "Heizen")
            + link([KNOWN["pump"], KNOWN["heating"]], "Kühlen")
        )
        pump.pages = [shared, whole]
        report, _, rests = await run_capture(pump, attempts=3)

    assert report is not None
    assert rests.count(A_MINUTE) == 1
    assert "heating" in by_page(report)


def test_an_optional_entry_sharing_its_link_is_not_taken():
    menu = CIRCUIT_MENU[2]
    page = column(
        link([menu, settings_segment(menu)], "Einstellungen")
        + link([menu, settings_segment(menu)], "Zeitprogramm")
    )
    walk = capture_tool._Walk()
    walk.read["circuit_menu"] = capture_tool.pages.children(page, STACK + menu)

    entry = capture_tool._entry(
        capture_tool.STEP_BY_KEY["circuit_settings"],
        walk,
        lambda _: pytest.fail("an optional page is never picked"),
    )

    assert entry is None


def test_a_modulation_form_of_another_type_is_not_taken():
    """The integration reads and sets a list form only."""
    path = STACK + f"{PUMP_MENU},{MODULATION}"
    text = modulation_form(50).replace('"para_list"', '"para_text"')

    assert (
        capture_tool._entries(capture_tool.STEP_BY_KEY["modulation"], text, path) == []
    )


@pytest.mark.parametrize(
    "half",
    [
        pytest.param(column(""), id="no form"),
        pytest.param(limit_leaf(60), id="another setting's form"),
    ],
)
async def test_a_modulation_page_without_its_own_form_is_asked_for_again(
    socket_enabled, half
):
    async with serving(StandInPump()) as pump:
        pump.site = with_optional_pages(site(GERMAN))
        whole = pump.site.pop(OPTIONAL_WALK[2])
        pump.pages = [half, whole]
        report, _, rests = await run_capture(pump)

    assert rests.count(A_MINUTE) == 1
    assert pump.asked.count(("GET", OPTIONAL_WALK[2])) == 2
    assert by_page(report)["modulation"]["offered"] == MODULATION_OFFERED


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


async def test_a_menu_served_with_nothing_to_open_is_asked_for_again(
    socket_enabled,
):
    """A menu that came with Service and Reset only had nothing left to
    offer: the tool asked for a number from 1 to 0 until stopped."""
    async with serving(StandInPump()) as pump:
        pump.site = site(GERMAN)
        whole = pump.site.pop(WALK[2])
        nothing_to_open = column(
            link([KNOWN["pump"], SERVICE], "Service")
            + link([KNOWN["pump"], RESET], "Reset")
        )
        pump.pages = [nothing_to_open, whole]
        report, questions, rests = await run_capture(pump)

    assert questions == []
    assert rests.count(A_MINUTE) == 1
    assert pump.asked == [
        *LOGIN,
        *asked(WALK[:5]),
        *LOGOUT,
        *LOGIN,
        *asked([WALK[2], WALK[5]]),
        *LOGOUT,
    ]
    assert by_page(report)["heating"]["entries"] == [
        ["Switching difference", "4.5 K"],
        ["Power limit", "60 %"],
    ]


async def test_a_picked_menu_with_nothing_to_open_is_picked_anew(socket_enabled):
    """Picked by the user, a menu offering nothing came back the same way in
    every one of the ten attempts."""
    codes = {**KNOWN, "pump": OTHER_MODEL["pump"]}
    settings = "50000001000000000080000A0B010002000301"
    async with serving(StandInPump()) as pump:
        pump.site = site(ENGLISH, codes)
        main = column(
            link([codes["info"]], ENGLISH["info"])
            + link([codes["pump"]], ENGLISH["pump"])
            + link([settings], "Settings")
        )
        pump.site[webif.OVERVIEW] = main
        pump.site[STACK + settings] = main + column(
            link([settings, SERVICE], "Service") + link([settings, RESET], "Reset")
        )
        report, questions, rests = await run_capture(pump, answers=["3", "y", "2", "y"])

    assert len(questions) == 4
    assert rests.count(A_MINUTE) == 1
    assert pump.asked.count(("GET", STACK + settings)) == 1
    assert by_page(report)["heating"]["entries"] == [
        ["Switching difference", "4.5 K"],
        ["Power limit", "60 %"],
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


async def test_every_request_is_held_to_the_time_limit_both_ways(
    socket_enabled, monkeypatch
):
    """The deadline and the socket's own limit both equal it. A deadline twice
    as long, or a socket without a limit - the only bound on connecting -
    passed every other test."""
    limits = []
    timer = threading.Timer

    def recorded_timer(interval, *args, **kwargs):
        limits.append(("deadline", interval))
        return timer(interval, *args, **kwargs)

    class RecordedConnection(http.client.HTTPConnection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            limits.append(("socket", self.timeout))

    monkeypatch.setattr(threading, "Timer", recorded_timer)
    monkeypatch.setattr(http.client, "HTTPConnection", RecordedConnection)
    async with serving(StandInPump()) as pump:
        pump.site = site(GERMAN)
        pump.failing[STATISTICS_PAGE] = 503
        await run_capture(pump, attempts=1)

    requests = len(pump.asked)
    assert limits.count(("deadline", capture_tool.TIMEOUT_SECONDS)) == requests
    assert limits.count(("socket", capture_tool.TIMEOUT_SECONDS)) == requests
    assert len(limits) == 2 * requests


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
        pump.site = with_optional_pages(site(GERMAN))
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
        # The names the user gave the circuits, shown beside their menus.
        "FBH",
        "RADIATOREN",
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


@pytest.mark.parametrize(
    "name",
    [
        "INFO",
        "HEAT_PUMP",
        "STATISTICS",
        "HEATING",
        "HEATING_CIRCUIT",
        "SECOND_GENERATOR",
        "HOT_WATER",
        "SETTINGS",
        "MODULATION_TITLE",
    ],
)
def test_the_tool_looks_for_the_titles_the_integration_finds(name):
    assert getattr(capture_tool, name) == getattr(discovery, name)


def test_the_tool_takes_the_form_type_the_integration_sets():
    assert capture_tool.SETTING_LIST == setting.SETTING_LIST


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
    """Without a form: the client's one save is a write the tool never makes."""
    assert capture_tool._allowed(method, path) == webif._allowed(method, path, None)


def test_a_web_address_is_refused_before_anything_is_asked(monkeypatch):
    """Given as a URL, the address ended in a traceback - after the user had
    typed the user name and the password."""
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("asked"))
    monkeypatch.setattr(capture_tool, "capture", lambda *_arguments: pytest.fail("ran"))

    with pytest.raises(SystemExit):
        capture_tool.main(["http://192.0.2.1/"])


def test_an_address_typed_with_a_space_is_read_without_it(monkeypatch):
    """The prompt keeps what was pasted: a trailing space ended in a traceback
    after the user name and the password, where the integration strips it."""
    monkeypatch.setattr("builtins.input", lambda _prompt: USER)
    monkeypatch.setattr(capture_tool.getpass, "getpass", lambda _prompt: PASSWORD)
    hosts = []

    def captured(host, *_arguments):
        hosts.append(host)

    monkeypatch.setattr(capture_tool, "capture", captured)

    capture_tool.main([" 192.0.2.10 "])

    assert hosts == ["192.0.2.10"]


@pytest.mark.parametrize("typed", ["", "   ", "192.0.2. 10"])
def test_an_address_that_cannot_be_dialled_is_refused_before_anything_is_asked(
    monkeypatch, typed
):
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("asked"))
    monkeypatch.setattr(capture_tool, "capture", lambda *_arguments: pytest.fail("ran"))

    with pytest.raises(SystemExit):
        capture_tool.main([typed])


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


def _run_on_the_users_python() -> list[pathlib.Path]:
    """The tool and every file it loads by path: all run on the user's Python."""
    loaded = [
        value
        for name, value in vars(capture_tool).items()
        if name.endswith("_PATH") and isinstance(value, pathlib.Path)
    ]
    return [REPO / "tools" / "webif_capture.py", *loaded]


@pytest.mark.parametrize("path", _run_on_the_users_python(), ids=lambda path: path.name)
def test_the_formatter_keeps_what_the_tool_runs_readable_for_python_3_10(path):
    """The tool promises Python 3.10, the Windows starter brings 3.13; ruff
    otherwise writes for the Home Assistant's Python and drops these brackets,
    a form no Python before 3.14 can read."""
    source = "try:\n    pass\nexcept (ValueError, TypeError):\n    pass\n"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--stdin-filename",
            str(path),
            "-",
        ],
        input=source,
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=60,
        check=True,
    )

    assert result.stdout == source


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
