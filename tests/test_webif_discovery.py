"""Finding the pages to poll by their titles, against the stand-in's menus."""

import asyncio

import aiohttp
import pytest

from custom_components.weishaupt_modbus import config_flow
from custom_components.weishaupt_modbus.config_flow import (
    MissingTitles,
    read_web_interface,
)
from custom_components.weishaupt_modbus.webif import client as webif
from custom_components.weishaupt_modbus.webif.discovery import (
    HEAT_PUMP_PAGE,
    HEATING_PAGE,
    STATISTICS_PAGE,
    find_pages,
)
from homeassistant.helpers.aiohttp_client import async_create_clientsession

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


async def test_the_dialog_finds_the_pages_and_logs_out_again(hass, pump, monkeypatch):
    """The dialog's own short visit, with a session of its own."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)

    found = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert found[HEATING_PAGE] == STACK + f"{PUMP_MENU},{HEATING}"
    assert pump.asked[-1] == ("GET", webif.LOGOUT)


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
    assert pump.asked.count(("GET", PAGE_PATH)) == 2, "searched once more, no more"
    assert pump.asked[-1] == ("GET", webif.LOGOUT)


async def test_a_menu_served_half_twice_is_searched_once_more(hass, pump, monkeypatch):
    """User wish, 2026-10-03: the controller now and then serves a page half,
    twice in a row; the dialog tries again before it shows an error."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    whole_info = pump.site.pop(STACK + INFO)
    half_info = MAIN_MENUS + column(link([INFO, HEAT_PUMP_INFO], "Wärmepumpe"))
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
    while ("GET", webif.LOGOUT) not in pump.asked:
        await asyncio.sleep(0.01)

    visit.cancel()
    with pytest.raises(asyncio.CancelledError):
        await visit

    assert made[0].closed


async def test_a_refused_login_is_not_tried_twice(hass, pump, monkeypatch):
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)

    with pytest.raises(webif.LoginRefused):
        await read_web_interface(hass, pump.host, USER, "wrong")
    assert len(pump.forms) == 1
