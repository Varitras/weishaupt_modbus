"""Finding the pages to poll by their titles, against the stand-in's menus."""

import asyncio

import aiohttp
import pytest

from custom_components.weishaupt_modbus.config_flow import read_web_interface
from custom_components.weishaupt_modbus.webif import client as webif
from custom_components.weishaupt_modbus.webif.discovery import (
    HEAT_PUMP_PAGE,
    HEATING_PAGE,
    STATISTICS_PAGE,
    find_pages,
)

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
)

LOGIN = [("GET", webif.INDEX), ("POST", webif.LOGIN)]


@pytest.fixture
async def pump(socket_enabled):
    stand_in = StandInPump()
    stand_in.site = menu_site()
    async with serving(stand_in) as served:
        yield served


@pytest.fixture
async def client(pump):
    async with aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(unsafe=True)
    ) as session:
        yield webif.Client(
            session, pump.host, USER, PASSWORD, host_lock=asyncio.Lock(), gap=0
        )


async def test_the_pages_are_found_by_their_titles(client):
    assert await find_pages(client) == {
        HEAT_PUMP_PAGE: STACK + f"{INFO},{HEAT_PUMP_INFO}",
        STATISTICS_PAGE: STACK + f"{INFO},{STATISTICS_INFO}",
        HEATING_PAGE: STACK + f"{PUMP_MENU},{HEATING}",
    }


async def test_only_the_three_menus_are_opened(pump, client):
    """The heat pump menu also lists Reset. The overview comes twice: the
    login opens it, the search reads it."""
    await find_pages(client)

    assert pump.asked == [
        *LOGIN,
        ("GET", PAGE_PATH),
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
