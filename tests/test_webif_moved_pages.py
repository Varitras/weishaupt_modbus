"""Pages that moved to new addresses, found again while polling.

The controller gave its menus new codes once (2026-11-17, an Evoblock), and
every address the entry held brought the overview; all of the web
interface's sensors stayed unavailable until the entry was reconfigured.
Driven with a real client against the stand-in.
"""

import asyncio
import logging

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weishaupt_modbus import webif_coordinator
from custom_components.weishaupt_modbus.const import CONF, CONST
from custom_components.weishaupt_modbus.webif import client as webif
from custom_components.weishaupt_modbus.webif.discovery import (
    HEAT_PUMP_PAGE,
    HEATING_CIRCUIT_PAGE,
    HEATING_PAGE,
)
from custom_components.weishaupt_modbus.webif_coordinator import (
    FAILURES_TO_STOP,
    Page,
    WebifCoordinator,
    polled_pages,
)
from custom_components.weishaupt_modbus.webif_sensor import (
    OPTIONAL_TITLES,
    REQUIRED_TITLES,
    TITLES_SHOWN_OTHERWISE,
    WBB,
)
from custom_components.weishaupt_modbus.weishaupt_modbus_api.write_budget import (
    WriteBudget,
)
from homeassistant.exceptions import HomeAssistantError

from .locking import until
from .test_webif_setup import PAGES, SHOWN, SITE
from .webif_stand_in import (
    HEATING,
    INFO,
    PAGE_PATH,
    PASSWORD,
    PUMP_MENU,
    RESET,
    STACK,
    TOKEN,
    USER,
    StandInPump,
    column,
    limit_segment,
    link,
    main_menus,
    menu_site,
    serving,
    value,
)

NEW = "88B7"
NEWER = "0E0F"
DAY = 24 * 3600.0


def renumbered(path, token=NEW):
    return path.replace(TOKEN, token)


NEW_PAGES = {key: renumbered(path) for key, path in PAGES.items()}
SEARCH = [("GET", renumbered(STACK + INFO)), ("GET", renumbered(STACK + PUMP_MENU))]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
async def pump(socket_enabled):
    stand_in = StandInPump()
    stand_in.site = {**menu_site(), **SITE}
    async with serving(stand_in) as served:
        yield served


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(domain=CONST.DOMAIN, data={CONF.PAGES: dict(PAGES)})
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
async def coordinator(hass, entry, pump, clock):
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        client = webif.Client(
            session,
            pump.host,
            USER,
            PASSWORD,
            host_lock=asyncio.Lock(),
            pacing=webif.Pacing(),
            gap=0,
        )
        polled = polled_pages(
            PAGES,
            {},
            REQUIRED_TITLES[WBB],
            TITLES_SHOWN_OTHERWISE[WBB],
            OPTIONAL_TITLES[WBB],
        )
        yield WebifCoordinator(
            hass, entry, client, polled, budget=WriteBudget(0, 10), clock=clock
        )


async def next_round(coordinator, pump, clock):
    """A round a day on, every page due; the pages and menus it asked for."""
    pump.asked.clear()
    clock.now += DAY
    await coordinator.async_refresh()
    return [request for request in pump.asked if request[1].startswith(PAGE_PATH)]


def failures(coordinator):
    return {
        key: state["failures"]
        for key, state in coordinator.diagnostics()["page_states"].items()
    }


async def test_moved_pages_are_found_again_from_the_overview(
    coordinator, pump, clock, entry, caplog
):
    """Four requests: the overview in place of the first page, Info, the
    heat pump menu, and the page at its new address. The overview is not
    asked for again, nor the old address a second time."""
    await next_round(coordinator, pump, clock)
    pump.renumber(NEW)
    caplog.set_level(logging.INFO)

    asked = await next_round(coordinator, pump, clock)

    assert asked == [
        ("GET", PAGES[HEAT_PUMP_PAGE]),
        *SEARCH,
        *(("GET", path) for path in NEW_PAGES.values()),
    ]
    assert coordinator.data == SHOWN
    assert set(failures(coordinator).values()) == {0}
    assert coordinator.diagnostics()["searched_again"] == 1
    ours = [
        record.getMessage()
        for record in caplog.records
        if record.name == webif_coordinator.__name__ and record.levelno >= logging.INFO
    ]
    assert ours == ["The pages moved to new addresses and were found again"]


async def test_the_addresses_found_again_are_kept_in_the_entry(
    coordinator, pump, clock, entry
):
    """Kept, a restart starts from them; and a page the entry has but does
    not poll is found again too."""
    data = {**entry.data, CONF.PAGES: {**PAGES, "unpolled": "/kept"}}
    coordinator.hass.config_entries.async_update_entry(entry, data=data)
    pump.renumber(NEW)

    await next_round(coordinator, pump, clock)

    assert entry.data[CONF.PAGES] == {**NEW_PAGES, "unpolled": "/kept"}
    assert (await next_round(coordinator, pump, clock)) == [
        ("GET", path) for path in NEW_PAGES.values()
    ]


async def test_the_overview_under_the_old_codes_is_asked_again_not_searched(
    coordinator, pump, clock, entry
):
    """A page served half: one more ask mends it, a search would not."""
    whole = pump.site.pop(PAGES[HEAT_PUMP_PAGE])
    pump.pages = [pump.site[PAGE_PATH], whole]

    asked = await next_round(coordinator, pump, clock)

    assert asked[:2] == [("GET", PAGES[HEAT_PUMP_PAGE])] * 2
    assert not set(SEARCH) & set(asked)
    assert entry.data[CONF.PAGES] == PAGES


async def test_pages_moved_again_at_their_new_addresses_are_not_searched_twice(
    coordinator, pump, clock, entry
):
    """The codes changed once more right after the search: each page fails
    with one request, and the round searches no second time."""
    pump.renumber(NEW)
    for path in NEW_PAGES.values():
        pump.site[path] = pump.site[PAGE_PATH].replace(NEW, NEWER)

    asked = await next_round(coordinator, pump, clock)

    assert asked == [
        ("GET", PAGES[HEAT_PUMP_PAGE]),
        *SEARCH,
        *(("GET", path) for path in NEW_PAGES.values()),
    ]
    assert set(failures(coordinator).values()) == {1}


async def test_a_failed_search_is_a_failure_of_the_page(
    coordinator, pump, clock, entry
):
    pump.renumber(NEW)
    pump.failing[renumbered(STACK + INFO)] = 500

    asked = await next_round(coordinator, pump, clock)

    assert asked == [("GET", PAGES[HEAT_PUMP_PAGE]), SEARCH[0]]
    assert failures(coordinator)[HEAT_PUMP_PAGE] == 1
    assert entry.data[CONF.PAGES] == PAGES
    assert coordinator.diagnostics()["searched_again"] == 0


async def test_a_stop_ends_once_the_moved_pages_are_found_again(
    coordinator, pump, clock, entry
):
    """An entry stopped before the codes changed asks its stopping page
    alone, at its old address."""
    pump.failing[PAGES[HEAT_PUMP_PAGE]] = 500
    for _ in range(FAILURES_TO_STOP):
        await next_round(coordinator, pump, clock)
    assert coordinator.diagnostics()["stopped_by"] is not None
    del pump.failing[PAGES[HEAT_PUMP_PAGE]]
    pump.renumber(NEW)

    asked = await next_round(coordinator, pump, clock)
    assert asked == [
        ("GET", PAGES[HEAT_PUMP_PAGE]),
        *SEARCH,
        ("GET", NEW_PAGES[HEAT_PUMP_PAGE]),
    ]
    assert coordinator.diagnostics()["stopped_by"] is None

    assert sorted(await next_round(coordinator, pump, clock)) == sorted(
        ("GET", path) for path in NEW_PAGES.values()
    )


async def test_codes_changed_twice_are_found_twice(coordinator, pump, clock, entry):
    """One search a round, not one for good: the next change is found too."""
    pump.renumber(NEW)
    await next_round(coordinator, pump, clock)
    pump.renumber(NEWER)

    await next_round(coordinator, pump, clock)

    assert entry.data[CONF.PAGES] == {
        key: renumbered(path, NEWER) for key, path in PAGES.items()
    }
    assert coordinator.data == SHOWN
    assert coordinator.diagnostics()["searched_again"] == 2


async def test_a_page_the_search_does_not_find_again_is_a_failure(
    hass, entry, pump, clock, caplog
):
    """An optional page Info no longer lists: the page that set the search
    off fails, not the code on a missing key."""
    circuit = STACK + f"{INFO},0C000C40000000000000000A0B020003000401"
    pump.site[circuit] = column(value("AT Mittelwert", "4.5 °C"))
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        client = webif.Client(
            session,
            pump.host,
            USER,
            PASSWORD,
            host_lock=asyncio.Lock(),
            pacing=webif.Pacing(),
            gap=0,
        )
        polled = polled_pages(
            {**PAGES, HEATING_CIRCUIT_PAGE: circuit},
            {},
            REQUIRED_TITLES[WBB],
            TITLES_SHOWN_OTHERWISE[WBB],
            OPTIONAL_TITLES[WBB],
        )
        polled.append(
            Page(
                HEATING_CIRCUIT_PAGE,
                circuit,
                frozenset({"AT Mittelwert"}),
                polled[0].interval,
            )
        )
        coordinator = WebifCoordinator(
            hass, entry, client, polled, budget=WriteBudget(0, 10), clock=clock
        )
        pump.renumber(NEW)

        await next_round(coordinator, pump, clock)

    assert failures(coordinator)[HEAT_PUMP_PAGE] == 1
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert entry.data[CONF.PAGES] == PAGES


async def test_a_page_stopped_right_after_the_search_is_asked_at_its_new_address(
    coordinator, pump, clock
):
    """The stop came in the round of the search: its retries go where the
    search found the page, not back to the old address and a second search."""
    pump.failing[PAGES[HEAT_PUMP_PAGE]] = 500
    for _ in range(FAILURES_TO_STOP - 1):
        await next_round(coordinator, pump, clock)
    del pump.failing[PAGES[HEAT_PUMP_PAGE]]
    pump.renumber(NEW)
    pump.failing[NEW_PAGES[HEAT_PUMP_PAGE]] = 500
    await next_round(coordinator, pump, clock)
    assert coordinator.diagnostics()["stopped_by"] is not None

    assert (await next_round(coordinator, pump, clock)) == [
        ("GET", NEW_PAGES[HEAT_PUMP_PAGE])
    ]


async def test_heizen_and_kuehlen_at_one_link_send_no_save(
    coordinator, pump, clock, monkeypatch
):
    """Under the new codes the heat pump menu links both to one page, which
    may be the cooling one: the search fails rather than take it, and the
    power limit is not saved there."""
    monkeypatch.setattr(webif_coordinator, "DEBOUNCE_SECONDS", 0.05)
    pump.show_power_limit(50)
    pump.site[STACK + PUMP_MENU] = main_menus() + column(
        link([PUMP_MENU, HEATING], "Heizen")
        + link([PUMP_MENU, HEATING], "Kühlen")
        + link([PUMP_MENU, RESET], "Reset")
    )
    pump.renumber(NEW)
    await next_round(coordinator, pump, clock)

    with pytest.raises(HomeAssistantError):
        await coordinator.set_power_limit(60)

    assert pump.saved == []
    assert coordinator.diagnostics()["searched_again"] == 0


async def test_a_write_asked_for_during_a_search_reads_and_saves_at_the_new_address(
    coordinator, pump, clock, monkeypatch
):
    """The write waits for the round's page and its search, then reads the
    heating page, its limit and the page the save leads to under the new
    codes; nothing goes to an old address after the overview came."""
    monkeypatch.setattr(webif_coordinator, "DEBOUNCE_SECONDS", 0.05)
    pump.show_power_limit(50)
    await next_round(coordinator, pump, clock)
    pump.renumber(NEW)
    info = renumbered(STACK + INFO)
    pump.delays[info] = 0.2
    pump.asked.clear()
    clock.now += DAY
    polling = asyncio.create_task(coordinator.async_refresh())
    await until(lambda: ("GET", info) in pump.asked)

    await coordinator.set_power_limit(60)
    await polling

    assert [form["stack"] for form in pump.saved] == [
        pump.current(f"{PUMP_MENU},{HEATING},{limit_segment(50)}")
    ]
    asked = [path for _, path in pump.asked]
    assert asked[0] == PAGES[HEAT_PUMP_PAGE]
    assert not [path for path in asked[1:] if TOKEN in path]
    assert NEW_PAGES[HEATING_PAGE] in asked
    assert coordinator.data[HEATING_PAGE]["Leistungsbegrenzung"] == "60 %"
