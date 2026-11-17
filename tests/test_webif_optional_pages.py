"""The pages Info shows only on some pumps: a heating circuit's and the 2nd WEZ's.

User wish, 2026-11-17: the averaged outdoor temperatures the heating curve
follows, and the switching cycles of each heating rod stage, which Modbus
does not carry. Seen on a WEB 13/20 set to German alone.
"""

import asyncio
from datetime import timedelta

import aiohttp
import pytest

from custom_components.weishaupt_modbus.const import CONF, CONST
from custom_components.weishaupt_modbus.webif import client as webif
from custom_components.weishaupt_modbus.webif.discovery import (
    HEAT_PUMP_PAGE,
    HEATING_CIRCUIT_PAGE,
    HEATING_PAGE,
    SECOND_GENERATOR_PAGE,
    STATISTICS_PAGE,
    find_pages,
)
from custom_components.weishaupt_modbus.webif_coordinator import polled_pages
from custom_components.weishaupt_modbus.webif_sensor import (
    ENGLISH_TITLES,
    EVOBLOCK,
    OPTIONAL_TITLES,
    REQUIRED_TITLES,
    WBB,
)
from custom_components.weishaupt_modbus.webif_visit import read_web_interface

from .test_webif_evoblock import ROWS, _state, heat_pump_page, shown
from .test_webif_setup import (
    PAGES,
    SITE,
    _enable_custom_integrations,  # noqa: F401
    _entries,
    _start,
)
from .webif_stand_in import (
    HEAT_PUMP_INFO,
    INFO,
    MAIN_MENUS,
    PASSWORD,
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

# Made-up segments one level below Info, shaped like the controller's.
SECOND_CIRCUIT = "0C000D1A000000000000000A0B020003000401"
THIRD_CIRCUIT = "0C000D1B000000000000000A0B020003000401"
SECOND_GENERATOR = "0C000D23000000000000000A0B020003000401"
OPTIONAL = {
    HEATING_CIRCUIT_PAGE: STACK + f"{INFO},{SECOND_CIRCUIT}",
    SECOND_GENERATOR_PAGE: STACK + f"{INFO},{SECOND_GENERATOR}",
}


def info_menu(*extra):
    """Info as an Evoblock lists it, a circuit's own name after its title."""
    return MAIN_MENUS + column(
        link([INFO, THIRD_CIRCUIT], "Heizkreis 3", "RADIATOREN")
        + link([INFO, SECOND_CIRCUIT], "Heizkreis 2", "FBH")
        + link([INFO, HEAT_PUMP_INFO], "Wärmepumpe")
        + link([INFO, SECOND_GENERATOR], "2. WEZ")
        + link([INFO, STATISTICS_INFO], "Statistik")
        + "".join(extra)
    )


def values_page(rows, values=None):
    """The rows, each with its sample value unless `values` gives another."""
    values = values or {}
    return column(
        "".join(value(title, values.get(title, shown(unit))) for title, unit in rows)
    )


# One value each, so that one read from the wrong row shows.
DISTINCT = {
    "AT Mittelwert": "4.5 °C",
    "AT Langzeitwert": "6.5 °C",
    "Schaltspiele E1": "1234",
    "Schaltspiele E2": "567",
}


def without(key, title):
    return [row for row in ROWS[key] if row[0] != title]


@pytest.fixture
async def pump(socket_enabled, monkeypatch):
    """An Evoblock whose Info lists both optional pages."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    stand_in = StandInPump()
    stand_in.site = {
        **menu_site(),
        **SITE,
        STACK + INFO: info_menu(),
        PAGES[HEAT_PUMP_PAGE]: heat_pump_page(ROWS[HEAT_PUMP_PAGE]),
        OPTIONAL[HEATING_CIRCUIT_PAGE]: values_page(
            ROWS[HEATING_CIRCUIT_PAGE], DISTINCT
        ),
        OPTIONAL[SECOND_GENERATOR_PAGE]: values_page(
            ROWS[SECOND_GENERATOR_PAGE], DISTINCT
        ),
    }
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


async def test_the_first_circuit_and_the_2nd_wez_are_found(client):
    """The averages are the same on every circuit: one page is read."""
    assert await find_pages(client) == {**PAGES, **OPTIONAL}


async def test_a_pump_whose_info_lists_neither_is_found_as_before(pump, client):
    pump.site[STACK + INFO] = menu_site()[STACK + INFO]

    assert await find_pages(client) == PAGES


async def test_an_optional_entry_shown_twice_is_left_out(pump, client):
    """Two links of one title leave no way to tell which one is the page."""
    pump.site[STACK + INFO] = info_menu(
        link([INFO, "0C000D24000000000000000A0B020003000401"], "2. WEZ")
    )

    found = await find_pages(client)

    assert SECOND_GENERATOR_PAGE not in found
    assert found[HEATING_CIRCUIT_PAGE] == OPTIONAL[HEATING_CIRCUIT_PAGE]


async def test_the_dialog_keeps_the_optional_pages_of_an_evoblock(hass, pump):
    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert visited.model == EVOBLOCK
    assert visited.pages == {**PAGES, **OPTIONAL}
    for path in OPTIONAL.values():
        assert pump.asked.count(("GET", path)) == 1


async def test_a_wbb_neither_keeps_nor_reads_them(hass, pump):
    """No sensor of a WBB reads them: no title of theirs was seen there."""
    pump.site.update(SITE)

    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert visited.model == WBB
    assert visited.pages == PAGES
    for path in OPTIONAL.values():
        assert ("GET", path) not in pump.asked


@pytest.mark.parametrize(
    ("key", "title"),
    [
        (HEATING_CIRCUIT_PAGE, "AT Mittelwert"),
        (SECOND_GENERATOR_PAGE, "Schaltspiele E2"),
    ],
)
async def test_an_optional_page_lacking_a_row_costs_only_itself(hass, pump, key, title):
    """Another firmware's page: its values go, the entry stays."""
    pump.site[OPTIONAL[key]] = values_page(without(key, title))

    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert key not in visited.pages
    assert visited.pages[HEATING_PAGE] == PAGES[HEATING_PAGE]


@pytest.mark.parametrize("key", [HEATING_CIRCUIT_PAGE, SECOND_GENERATOR_PAGE])
@pytest.mark.parametrize(
    "answer",
    [column(""), "blank"],
    ids=["served half", "a value blank"],
)
async def test_an_optional_page_unclear_in_both_searches_costs_only_itself(
    hass, pump, key, answer
):
    """Served half, or with a value blank, also in the second search: the
    page goes, the dialog does not fail."""
    title = min(REQUIRED_TITLES[EVOBLOCK][key])
    pump.site[OPTIONAL[key]] = (
        values_page(ROWS[key], {title: ""}) if answer == "blank" else answer
    )

    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert visited.pages == {
        **PAGES,
        **{other: path for other, path in OPTIONAL.items() if other != key},
    }
    # Twice in each search.
    assert pump.asked.count(("GET", OPTIONAL[key])) == 4


async def test_an_optional_page_the_second_search_brings_whole_is_kept(hass, pump):
    """A hiccup of the controller costs a second search, not the page."""
    path = OPTIONAL[HEATING_CIRCUIT_PAGE]
    del pump.site[path]
    pump.pages = [column(""), column(""), values_page(ROWS[HEATING_CIRCUIT_PAGE])]

    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert visited.pages == {**PAGES, **OPTIONAL}
    assert pump.asked.count(("GET", path)) == 3


def test_only_an_evoblock_has_sensors_on_them():
    assert set(REQUIRED_TITLES[WBB]) == {HEAT_PUMP_PAGE, STATISTICS_PAGE, HEATING_PAGE}
    assert REQUIRED_TITLES[EVOBLOCK][HEATING_CIRCUIT_PAGE] == {
        "AT Mittelwert",
        "AT Langzeitwert",
    }
    assert REQUIRED_TITLES[EVOBLOCK][SECOND_GENERATOR_PAGE] == {
        "Schaltspiele E1",
        "Schaltspiele E2",
    }


@pytest.mark.parametrize("model", [WBB, EVOBLOCK])
def test_an_optional_page_is_polled_only_where_found_and_read(model):
    """An entry made before them polls as it did; a WBB, found them or not,
    has no sensor on them."""

    def polled(paths):
        return {
            page.key: page
            for page in polled_pages(
                paths,
                {},
                REQUIRED_TITLES[model],
                ENGLISH_TITLES[model],
                OPTIONAL_TITLES[model],
            )
        }

    assert set(polled(PAGES)) == set(PAGES)
    found = polled({**PAGES, **OPTIONAL})
    if model == WBB:
        assert set(found) == set(PAGES)
    else:
        assert set(found) == {*PAGES, *OPTIONAL}
        for key in OPTIONAL:
            assert found[key].interval == timedelta(
                minutes=CONST.WEBIF_SLOW_INTERVAL_MINUTES
            )
            assert not found[key].menu


@pytest.mark.e2e
@pytest.mark.timeout(120)
async def test_an_evoblock_entry_reads_the_averages_and_the_stage_cycles(hass, pump):
    web = _entries(hass, pump)
    hass.config_entries.async_update_entry(
        web,
        data={**web.data, CONF.MODEL: EVOBLOCK, CONF.PAGES: {**PAGES, **OPTIONAL}},
    )
    await _start(hass, web)

    assert _state(hass, "at_mittelwert") == "4.5"
    assert _state(hass, "at_langzeitwert") == "6.5"
    assert _state(hass, "schaltspiele_e1") == "1234.0"
    assert _state(hass, "schaltspiele_e2") == "567.0"
    for path in OPTIONAL.values():
        assert pump.asked.count(("GET", path)) == 1


@pytest.mark.e2e
@pytest.mark.timeout(120)
async def test_an_evoblock_entry_made_before_them_reads_as_before(hass, pump):
    """Reconfiguring the entry finds them; until then nothing changes."""
    web = _entries(hass, pump)
    hass.config_entries.async_update_entry(web, data={**web.data, CONF.MODEL: EVOBLOCK})
    await _start(hass, web)

    assert _state(hass, "hochdruck") == "12.0"
    assert _state(hass, "at_mittelwert") is None
    for path in OPTIONAL.values():
        assert ("GET", path) not in pump.asked
