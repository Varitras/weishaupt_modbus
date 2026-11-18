"""Finding the pages to poll by their titles, against the stand-in's menus."""

import asyncio

import aiohttp
import pytest

from custom_components.weishaupt_modbus import webif_visit
from custom_components.weishaupt_modbus.configentry import HOST_LOCKS
from custom_components.weishaupt_modbus.webif import client as webif
from custom_components.weishaupt_modbus.webif.discovery import (
    HEAT_PUMP_PAGE,
    HEATING_PAGE,
    STATISTICS_PAGE,
    DoubledMenuEntries,
    MissingMenuEntries,
    find_pages,
    moved,
)
from custom_components.weishaupt_modbus.webif_visit import (
    MissingTitles,
    UnclearValues,
    UnknownUnits,
    read_web_interface,
)
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .locking import WatchedLock, until
from .test_webif_setup import PAGES, SHOWN, SITE, english_site
from .webif_stand_in import (
    ENGLISH_MENUS,
    HEAT_PUMP_INFO,
    HEATING,
    INFO,
    MAIN_MENUS,
    PAGE_PATH,
    PASSWORD,
    PUMP_MENU,
    RESET,
    STACK,
    STATISTICS_INFO,
    TOKEN,
    USER,
    StandInPump,
    column,
    link,
    main_menus,
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


SECOND_GENERATOR_INFO = "0C000C30000000000000000A0B020003000401"
INFO_WITH_A_DOUBLE = MAIN_MENUS + column(
    link([INFO, HEAT_PUMP_INFO], "Wärmepumpe")
    + link([INFO, STATISTICS_INFO], "Statistik")
    + link([INFO, "0C000C24000000000000000A0B020003000401"], "Wärmepumpe")
)


async def test_a_menu_entry_shown_twice_is_not_taken_at_random(pump, client):
    """The last of two links of the same title was stored in the entry for
    good, whichever page it led to; refused, the entry went unnamed."""
    pump.site[STACK + INFO] = INFO_WITH_A_DOUBLE

    with pytest.raises(DoubledMenuEntries) as raised:
        await find_pages(client)

    assert raised.value.titles == {"Wärmepumpe"}


async def test_two_titles_at_one_link_are_not_taken(pump, client):
    """The link may lead to either page: Heizen's to the cooling one, whose
    power limit a write would then set."""
    pump.site[STACK + PUMP_MENU] = MAIN_MENUS + column(
        link([PUMP_MENU, HEATING], "Heizen")
        + link([PUMP_MENU, HEATING], "Kühlen")
        + link([PUMP_MENU, RESET], "Reset")
    )

    with pytest.raises(DoubledMenuEntries) as raised:
        await find_pages(client)

    assert raised.value.titles == {"Heizen"}
    assert pump.asked.count(("GET", STACK + PUMP_MENU)) == 2


async def test_an_entry_not_wanted_at_another_ones_link_is_left_out(pump, client):
    """Neither of the two is taken, and the menu does not fail."""
    pump.site[STACK + INFO] = MAIN_MENUS + column(
        link([INFO, HEAT_PUMP_INFO], "Wärmepumpe")
        + link([INFO, STATISTICS_INFO], "Statistik")
        + link([INFO, SECOND_GENERATOR_INFO], "2. WEZ")
        + link([INFO, SECOND_GENERATOR_INFO], "Heizkreis 2")
    )

    found = await find_pages(client)

    assert found == {
        HEAT_PUMP_PAGE: STACK + f"{INFO},{HEAT_PUMP_INFO}",
        STATISTICS_PAGE: STACK + f"{INFO},{STATISTICS_INFO}",
        HEATING_PAGE: STACK + f"{PUMP_MENU},{HEATING}",
    }
    assert pump.asked.count(("GET", STACK + INFO)) == 1


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


def renumbered(text, token="88B7"):
    """The text with the codes a controller gave its menus anew."""
    return text.replace(TOKEN, token)


HEAT_PUMP_PATH = PAGES[HEAT_PUMP_PAGE]
HEATING_PATH = PAGES[HEATING_PAGE]


@pytest.mark.parametrize(
    ("path", "overview"),
    [
        (HEAT_PUMP_PATH, renumbered(MAIN_MENUS)),
        (HEATING_PATH, renumbered(MAIN_MENUS)),
        (HEAT_PUMP_PATH, renumbered(main_menus(ENGLISH_MENUS))),
    ],
    ids=["below Info", "below the heat pump menu", "in English"],
)
def test_the_overview_under_new_codes_says_the_page_moved(path, overview):
    """As the controller answered an address of its old codes, 2026-11-17."""
    assert moved(overview, path)


@pytest.mark.parametrize(
    "answer",
    [
        MAIN_MENUS,
        MAIN_MENUS + column(""),
        MAIN_MENUS + column(link([INFO, PUMP_MENU], "Wärmepumpe")),
        column(link([INFO, INFO], "Info") + link([INFO, PUMP_MENU], "Wärmepumpe")),
        SITE[HEAT_PUMP_PATH],
        renumbered(column(link([INFO], "Info"))),
        renumbered(MAIN_MENUS + column(link([INFO], "Info"))),
        renumbered(column(link([INFO], "Info") + link([INFO], "Wärmepumpe"))),
        "",
    ],
    ids=[
        "the overview under the old codes",
        "an empty column",
        "the main menus nested below",
        "the main menus nested alone",
        "the page itself",
        "no heat pump menu",
        "Info twice",
        "Info and the heat pump menu at one link",
        "nothing",
    ],
)
def test_an_answer_served_half_or_whole_says_no_page_moved(answer):
    """A page served half shows the main menus too, under its own codes:
    taken for a moved one, it would cost a search instead of one more ask."""
    assert not moved(answer, HEAT_PUMP_PATH)


async def test_a_search_from_an_overview_does_not_ask_for_it(pump, client):
    """The overview in place of a page is the one the search starts from."""
    pump.renumber("88B7")

    found = await find_pages(client, pump.site[PAGE_PATH])

    assert found == {key: renumbered(path) for key, path in PAGES.items()}
    assert pump.asked == [
        *LOGIN,
        ("GET", renumbered(STACK + INFO)),
        ("GET", renumbered(STACK + PUMP_MENU)),
    ]


async def test_a_search_from_an_overview_without_its_menus_asks_nothing(pump, client):
    with pytest.raises(MissingMenuEntries) as raised:
        await find_pages(client, column(link([INFO], "Info")))

    assert raised.value.titles == {"Wärmepumpe"}
    assert pump.asked == []


async def test_the_dialog_finds_the_pages_and_logs_out_again(hass, pump, monkeypatch):
    """The dialog's own short visit, with a session of its own."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)

    found = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert found.pages[HEATING_PAGE] == STACK + f"{PUMP_MENU},{HEATING}"
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

    assert (await visit).pages[HEATING_PAGE] == STACK + f"{PUMP_MENU},{HEATING}"
    assert all(pump.held), "the dialog asked without its pump's lock"


LANGUAGES = [
    pytest.param({**menu_site(), **SITE}, id="German"),
    pytest.param({**menu_site(ENGLISH_MENUS), **english_site()}, id="English"),
]


@pytest.mark.parametrize("site", LANGUAGES)
async def test_the_dialog_reads_each_page_it_found_once(hass, pump, monkeypatch, site):
    """User decision, 2026-10-03: the dialog reads the pages it will poll, so
    a page another model shows differently fails there and not ten minutes
    later in the brake. User wish, 2026-10-10: set to English, the controller
    shows its menus and values under other titles, and the dialog found none
    of them."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    pump.site = dict(site)

    found = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert found.pages == PAGES
    assert pump.asked == [
        *LOGIN,
        ("GET", PAGE_PATH),
        ("GET", STACK + INFO),
        ("GET", STACK + PUMP_MENU),
        *(("GET", path) for path in found.pages.values()),
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

    assert raised.value.page == "Info › Wärmepumpe (information › heat pump)"
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

    assert raised.value.page == "Wärmepumpe › Heizen (heat pump › heating)"
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


async def test_a_menu_entry_shown_twice_on_every_visit_is_named(
    hass, pump, monkeypatch
):
    """Now and then a hiccup, which a second search mends; on every visit,
    the dialog said the pages had come only in part."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    pump.site[STACK + INFO] = INFO_WITH_A_DOUBLE

    with pytest.raises(DoubledMenuEntries) as raised:
        await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert raised.value.titles == {"Wärmepumpe"}
    assert pump.asked.count(("GET", PAGE_PATH)) == 2, "searched once more"


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

    assert raised.value.page == "Info › Wärmepumpe (information › heat pump)"
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

    assert raised.value.page == "Info › Wärmepumpe (information › heat pump)"
    assert raised.value.titles == {"Hochdruck"}
    assert pump.asked.count(("GET", PAGE_PATH)) == 1


async def test_a_heating_menu_served_half_on_every_ask_is_not_called_unsupported(
    hass, pump, monkeypatch
):
    """The heating settings are a menu read for its entries: an empty column
    there is a page served half, which a second search may mend - not a menu
    lacking every title its sensors read."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    pump.site[PAGES[HEATING_PAGE]] = column("")

    with pytest.raises(webif.Broken) as raised:
        await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert type(raised.value) is webif.Broken
    assert pump.asked.count(("GET", PAGE_PATH)) == 2, "searched once more"


@pytest.mark.parametrize("off", ["Aus", "off"], ids=["German", "English"])
async def test_no_value_and_off_are_no_unknown_unit(hass, pump, monkeypatch, off):
    """The controller's own words for no value and for an idle power; set to
    English it writes "off", and the dialog refused an idle pump. On a value
    that does not read it as 0, the word is no reading, not another unit."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    shown_instead = {"Hochdruck": "--", "Ist Leistung": off, "Mitteldruck": off}
    entries = [
        (title, shown_instead.get(title, shown)) for title, shown in HEAT_PUMP_SHOWN
    ]
    pump.site[PAGES[HEAT_PUMP_PAGE]] = heat_pump_page(entries)

    found = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert found.pages[HEAT_PUMP_PAGE] == PAGES[HEAT_PUMP_PAGE]


async def test_a_menu_served_half_twice_is_searched_once_more(hass, pump, monkeypatch):
    """User wish, 2026-10-03: the controller now and then serves a page half,
    twice in a row; the dialog tries again before it shows an error."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    whole_info = pump.site.pop(STACK + INFO)
    half_info = MAIN_MENUS + column("")
    pump.pages = [half_info, half_info, whole_info]

    found = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert found.pages[STATISTICS_PAGE] == STACK + f"{INFO},{STATISTICS_INFO}"
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

    monkeypatch.setattr(webif_visit, "async_create_clientsession", recorded)
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
