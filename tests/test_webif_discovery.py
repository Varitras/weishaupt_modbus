"""Finding the pages to poll by their titles, against the stand-in's menus."""

import asyncio

import aiohttp
import pytest

from custom_components.weishaupt_modbus import config_flow
from custom_components.weishaupt_modbus.config_flow import (
    MissingTitles,
    UnclearValues,
    UnknownUnits,
    read_web_interface,
)
from custom_components.weishaupt_modbus.configentry import HOST_LOCKS
from custom_components.weishaupt_modbus.webif import client as webif
from custom_components.weishaupt_modbus.webif.discovery import (
    HEAT_PUMP_PAGE,
    HEATING_PAGE,
    STATISTICS_PAGE,
    MissingMenuEntries,
    find_pages,
)
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .locking import WatchedLock, until
from .test_webif_setup import PAGES, SHOWN, SITE
from .webif_stand_in import (
    HEAT_PUMP_INFO,
    HEATING,
    INFO,
    MAIN_MENUS,
    PAGE_PATH,
    PASSWORD,
    PUMP_MENU,
    STACK,
    STATISTICS_INFO,
    USER,
    StandInPump,
    column,
    link,
    menu_site,
    serving,
    value,
)

LOGIN = [("GET", webif.INDEX), ("POST", webif.LOGIN)]
HEAT_PUMP_SHOWN = list(SHOWN[HEAT_PUMP_PAGE].items())


def heat_pump_page(entries):
    return column("".join(value(title, shown) for title, shown in entries))


def shown_otherwise(title, text):
    """The heat pump page's entries with one title showing text instead."""
    return [(name, text if name == title else shown) for name, shown in HEAT_PUMP_SHOWN]


@pytest.fixture
async def pump(socket_enabled):
    """The menus and the three pages they lead to."""
    stand_in = StandInPump()
    stand_in.site = {**menu_site(), **SITE}
    async with serving(stand_in) as served:
        yield served


@pytest.fixture
async def client(pump):
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        yield webif.Client(
            session,
            pump.host,
            USER,
            PASSWORD,
            host_lock=asyncio.Lock(),
            pacing=webif.Pacing(),
            gap=0,
        )


async def test_the_pages_are_found_by_their_titles(client):
    assert await find_pages(client) == {
        HEAT_PUMP_PAGE: STACK + f"{INFO},{HEAT_PUMP_INFO}",
        STATISTICS_PAGE: STACK + f"{INFO},{STATISTICS_INFO}",
        HEATING_PAGE: STACK + f"{PUMP_MENU},{HEATING}",
    }


async def test_only_the_three_menus_are_opened(pump, client):
    """The heat pump menu also lists Reset."""
    await find_pages(client)

    assert pump.asked == [
        *LOGIN,
        ("GET", PAGE_PATH),
        ("GET", STACK + INFO),
        ("GET", STACK + PUMP_MENU),
    ]


async def test_a_menu_without_the_entry_wanted_is_broken(pump, client):
    pump.site[STACK + INFO] = MAIN_MENUS + column(
        link([INFO, HEAT_PUMP_INFO], "Wärmepumpe")
    )

    with pytest.raises(webif.Broken):
        await find_pages(client)


async def test_an_overview_without_the_heat_pump_menu_is_broken(pump, client):
    pump.site[PAGE_PATH] = column(link([INFO], "Info"))

    with pytest.raises(webif.Broken):
        await find_pages(client)


async def test_a_menu_entry_shown_twice_is_not_taken_at_random(pump, client):
    """The last of two links of the same title was stored in the entry for
    good, whichever page it led to."""
    pump.site[STACK + INFO] = MAIN_MENUS + column(
        link([INFO, HEAT_PUMP_INFO], "Wärmepumpe")
        + link([INFO, STATISTICS_INFO], "Statistik")
        + link([INFO, "0C000C24000000000000000A0B020003000401"], "Wärmepumpe")
    )

    with pytest.raises(webif.Broken) as raised:
        await find_pages(client)

    assert type(raised.value) is webif.Broken


async def test_a_menu_served_half_once_is_read_from_its_second_answer(pump, client):
    """What the first answer showed must not pile onto the second's."""
    whole_info = pump.site.pop(STACK + INFO)
    pump.pages = [
        MAIN_MENUS + column(link([INFO, HEAT_PUMP_INFO], "Wärmepumpe")),
        whole_info,
    ]

    found = await find_pages(client)

    assert found[STATISTICS_PAGE] == STACK + f"{INFO},{STATISTICS_INFO}"


async def test_the_menu_entries_not_found_are_named(pump, client):
    """A controller set to another language shows its menus whole, under
    other names; the dialog said "try again", which could never help."""
    pump.site[STACK + INFO] = MAIN_MENUS + column(
        link([INFO, HEAT_PUMP_INFO], "Wärmepumpe")
    )

    with pytest.raises(MissingMenuEntries) as raised:
        await find_pages(client)

    assert raised.value.titles == {"Statistik"}


async def test_the_dialog_finds_the_pages_and_logs_out_again(hass, pump, monkeypatch):
    """The dialog's own short visit, with a session of its own."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)

    found = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert found[HEATING_PAGE] == STACK + f"{PUMP_MENU},{HEATING}"
    assert pump.asked[-1] == ("GET", webif.LOGOUT)


async def test_the_dialog_waits_while_its_pump_is_asked(hass, pump, monkeypatch):
    """Handed a lock of its own, the dialog would ask the controller beside a
    Modbus poll, and nothing showed it."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    lock = WatchedLock()
    hass.data.setdefault(HOST_LOCKS, {})[pump.host] = lock
    pump.watched_lock = lock

    async with lock:
        visit = hass.async_create_task(
            read_web_interface(hass, pump.host, USER, PASSWORD)
        )
        await until(lambda: lock.waited or pump.asked)
        assert pump.asked == []

    assert (await visit)[HEATING_PAGE] == STACK + f"{PUMP_MENU},{HEATING}"
    assert all(pump.held), "the dialog asked without its pump's lock"


async def test_the_dialog_reads_each_page_it_found_once(hass, pump, monkeypatch):
    """User decision, 2026-10-03: the dialog reads the pages it will poll, so
    a page another model shows differently fails there and not ten minutes
    later in the brake."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)

    found = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert pump.asked == [
        *LOGIN,
        ("GET", PAGE_PATH),
        ("GET", STACK + INFO),
        ("GET", STACK + PUMP_MENU),
        *(("GET", path) for path in found.values()),
        ("GET", webif.LOGOUT),
    ]


async def test_a_page_without_a_title_its_sensors_read_is_named(
    hass, pump, monkeypatch
):
    """Another model or firmware: the page came whole but lacked a title one
    of its sensors reads, the dialog went through, and the brake stopped every
    page ten minutes later without a word on why."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    missing = "EVI Sauggastemperatur"
    pump.site[PAGES[HEAT_PUMP_PAGE]] = column(
        "".join(
            value(title, shown)
            for title, shown in SHOWN[HEAT_PUMP_PAGE].items()
            if title != missing
        )
    )

    with pytest.raises(MissingTitles) as raised:
        await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert raised.value.page == "Info › Wärmepumpe"
    assert raised.value.titles == {missing}
    assert pump.asked.count(("GET", PAGE_PATH)) == 1, "a second search cannot help"
    assert pump.asked[-1] == ("GET", webif.LOGOUT)


async def test_a_heating_menu_with_other_entries_is_named(hass, pump, monkeypatch):
    """Another model's heating menu showed entries of its own level, none of
    them one the sensors read, and the dialog said it had come only in part:
    try again, which could never help."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    other_entry = "64001802000000001E40000A0B030011010401"
    pump.site[PAGES[HEATING_PAGE]] = column(
        link([PUMP_MENU, HEATING, other_entry], "Heizgrenze", "15.0 °C")
    )

    with pytest.raises(MissingTitles) as raised:
        await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert raised.value.page == "Wärmepumpe › Heizen"
    assert raised.value.titles == set(SHOWN[HEATING_PAGE])


async def test_a_page_is_judged_by_its_last_answer(hass, pump, monkeypatch):
    """The first answer came half, the second whole but without a title the
    sensors read: judged by the first, the dialog searched once more for
    nothing before it named the title."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    missing = "EVI Sauggastemperatur"
    lacking = column(
        "".join(
            value(title, shown)
            for title, shown in SHOWN[HEAT_PUMP_PAGE].items()
            if title != missing
        )
    )
    del pump.site[PAGES[HEAT_PUMP_PAGE]]
    pump.pages = [MAIN_MENUS + column(""), lacking]

    with pytest.raises(MissingTitles) as raised:
        await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert raised.value.titles == {missing}
    assert pump.asked.count(("GET", PAGE_PATH)) == 1


async def test_a_controller_in_another_language_is_searched_only_once(
    hass, pump, monkeypatch
):
    """Its menus come whole under other names on every visit: a second
    search only added load before the dialog said so."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    pump.site[STACK + INFO] = MAIN_MENUS + column(
        link([INFO, HEAT_PUMP_INFO], "Wärmepumpe")
    )

    with pytest.raises(MissingMenuEntries):
        await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert pump.asked.count(("GET", PAGE_PATH)) == 1


@pytest.mark.parametrize(
    "half",
    [
        MAIN_MENUS + column(""),
        MAIN_MENUS + column(link([INFO, PUMP_MENU], "Wärmepumpe")),
        SITE[PAGES[STATISTICS_PAGE]],
    ],
    ids=["empty column", "main menus nested", "another section"],
)
async def test_a_page_served_half_on_every_ask_is_not_called_unsupported(
    hass, pump, monkeypatch, half
):
    """Four half answers in a row - two visits, two asks each - and the
    dialog said the controller was not supported, naming every title of the
    page; asking again a little later succeeds."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    pump.site[PAGES[HEAT_PUMP_PAGE]] = half

    with pytest.raises(webif.Broken) as raised:
        await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert type(raised.value) is webif.Broken
    assert pump.asked.count(("GET", PAGE_PATH)) == 2, "searched once more"


async def test_a_menu_served_half_on_every_ask_is_not_called_another_language(
    hass, pump, monkeypatch
):
    """An empty column in place of the Info menu, four times, and the dialog
    asked whether the controller's language was German."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    pump.site[STACK + INFO] = MAIN_MENUS + column("")

    with pytest.raises(webif.Broken) as raised:
        await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert type(raised.value) is webif.Broken
    assert pump.asked.count(("GET", PAGE_PATH)) == 2, "searched once more"


@pytest.mark.parametrize(
    ("entries", "unclear"),
    [
        (shown_otherwise("Hochdruck", ""), {"Hochdruck"}),
        ([*HEAT_PUMP_SHOWN, ("Verdichter", "2568 rpm")], {"Verdichter"}),
        ([*HEAT_PUMP_SHOWN, ("Betrieb", "")], {"Betrieb"}),
    ],
    ids=["value left empty", "title twice", "unread entry left empty"],
)
async def test_values_shown_empty_or_twice_are_named(
    hass, pump, monkeypatch, entries, unclear
):
    """The dialog said the menus had come incomplete and to try again, which
    cannot help on a model that shows the page so."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    pump.site[PAGES[HEAT_PUMP_PAGE]] = heat_pump_page(entries)

    with pytest.raises(UnclearValues) as raised:
        await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert raised.value.page == "Info › Wärmepumpe"
    assert raised.value.titles == unclear
    assert pump.asked.count(("GET", PAGE_PATH)) == 2, "searched once more"


async def test_a_value_in_a_unit_its_sensor_does_not_read_is_named(
    hass, pump, monkeypatch
):
    """Another spelling of the unit passed the dialog, and the sensor read
    unknown for good without a word on why."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    pump.site[PAGES[HEAT_PUMP_PAGE]] = heat_pump_page(
        shown_otherwise("Hochdruck", "24.4 bar")
    )

    with pytest.raises(UnknownUnits) as raised:
        await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert raised.value.page == "Info › Wärmepumpe"
    assert raised.value.titles == {"Hochdruck"}


async def test_no_value_and_off_are_no_unknown_unit(hass, pump, monkeypatch):
    """The controller's own words for no value and for an idle power."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    entries = [
        (title, {"Hochdruck": "--", "Ist Leistung": "Aus"}.get(title, shown))
        for title, shown in HEAT_PUMP_SHOWN
    ]
    pump.site[PAGES[HEAT_PUMP_PAGE]] = heat_pump_page(entries)

    found = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert found[HEAT_PUMP_PAGE] == PAGES[HEAT_PUMP_PAGE]


async def test_a_menu_served_half_twice_is_searched_once_more(hass, pump, monkeypatch):
    """User wish, 2026-10-03: the controller now and then serves a page half,
    twice in a row; the dialog tries again before it shows an error."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    whole_info = pump.site.pop(STACK + INFO)
    half_info = MAIN_MENUS + column("")
    pump.pages = [half_info, half_info, whole_info]

    found = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert found[STATISTICS_PAGE] == STACK + f"{INFO},{STATISTICS_INFO}"
    assert len(pump.forms) == 1, "searched again in the same session"


async def test_an_error_status_is_not_searched_once_more(hass, pump, monkeypatch):
    """An error status counted as a menu served half, so the dialog asked
    the struggling server for the whole search a second time."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    pump.status = 500

    with pytest.raises(webif.Unreachable):
        await read_web_interface(hass, pump.host, USER, PASSWORD)
    assert pump.asked.count(("GET", PAGE_PATH)) == 1


async def test_a_visit_cancelled_in_its_logout_lets_go_of_its_session(
    hass, pump, monkeypatch
):
    """The dialog closed during the visit's last logout: the cancel cut the
    logout short, and the session the visit had made was never let go."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    made = []

    def recorded(*args, **kwargs):
        made.append(async_create_clientsession(*args, **kwargs))
        return made[-1]

    monkeypatch.setattr(config_flow, "async_create_clientsession", recorded)
    pump.delays[webif.LOGOUT] = 0.5
    visit = asyncio.create_task(read_web_interface(hass, pump.host, USER, PASSWORD))
    await until(lambda: ("GET", webif.LOGOUT) in pump.asked)

    visit.cancel()
    with pytest.raises(asyncio.CancelledError):
        await visit

    assert made[0].closed


async def test_a_refused_login_is_not_tried_twice(hass, pump, monkeypatch):
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)

    with pytest.raises(webif.LoginRefused):
        await read_web_interface(hass, pump.host, USER, "wrong")
    assert len(pump.forms) == 1
