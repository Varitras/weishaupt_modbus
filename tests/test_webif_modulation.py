"""An Evoblock's Wärmepumpe › Modulation: its output while the hot water loads.

User wish, 2026-11-18: set from Home Assistant, Automatik or 50 to 100 %,
instead of at the pump, where it was set from Automatik to 50 the day
before. Its entry in the heat pump menu leads straight to its form, with
no page of values before it. Seen on a WEB 13/20 set to German alone.
"""

import asyncio

import aiohttp
import pytest

from custom_components.weishaupt_modbus import webif_coordinator
from custom_components.weishaupt_modbus.const import CONF, CONST
from custom_components.weishaupt_modbus.webif import client as webif
from custom_components.weishaupt_modbus.webif.discovery import (
    MODULATION_PAGE,
    PAGE_MENUS,
    find_pages,
)
from custom_components.weishaupt_modbus.webif_coordinator import Page, polled_pages
from custom_components.weishaupt_modbus.webif_sensor import (
    ENGLISH_TITLES,
    EVOBLOCK,
    OPTIONAL_TITLES,
    REQUIRED_TITLES,
    WBB,
)
from custom_components.weishaupt_modbus.webif_visit import read_web_interface
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .test_webif_settings_pages import ALL_PAGES, evoblock_site
from .test_webif_setup import (
    SITE,
    _enable_custom_integrations,  # noqa: F401
    _entries,
    _start,
)
from .webif_stand_in import (
    AUTOMATIC,
    HEATING,
    MODULATION,
    MODULATION_PATH,
    PASSWORD,
    PUMP_MENU,
    RESET,
    STACK,
    TOKEN,
    USER,
    StandInPump,
    column,
    limit_leaf,
    link,
    main_menus,
    modulation_form,
    serving,
)

PUMP_MENU_PATH = STACK + PUMP_MENU
WITH_MODULATION = {**ALL_PAGES, MODULATION_PAGE: MODULATION_PATH}


def with_modulation(pump, selected=50, offered=None):
    """The heat pump menu lists the modulation; a save sets it, as live."""
    pump.site[pump.current(PUMP_MENU_PATH)] = pump.current(
        main_menus()
        + column(
            link([PUMP_MENU, HEATING], "Heizen")
            + link([PUMP_MENU, MODULATION], "Modulation")
            + link([PUMP_MENU, RESET], "Reset")
        )
    )
    show(pump, selected, offered)
    circuits = pump.saves_elsewhere

    def saves(form):
        if form["stack"] != pump.current(f"{PUMP_MENU},{MODULATION}"):
            return circuits(form)
        show(pump, int(form["value"]), offered)
        # Live, 2026-11-18: the controller answers with the heat pump menu.
        return pump.current(PUMP_MENU_PATH)

    pump.saves_elsewhere = saves


def show(pump, selected, offered=None):
    form = modulation_form(selected, *(() if offered is None else (offered,)))
    pump.site[pump.current(MODULATION_PATH)] = pump.current(form)


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(webif_coordinator, "DEBOUNCE_SECONDS", 0.05)


@pytest.fixture
async def pump(socket_enabled, monkeypatch):
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    stand_in = StandInPump()
    evoblock_site(stand_in)
    with_modulation(stand_in)
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


async def test_it_is_found_by_its_title_in_the_heat_pump_menu(client):
    assert (await find_pages(client))[MODULATION_PAGE] == MODULATION_PATH


async def test_it_is_found_again_under_new_codes(pump, client):
    """As the controller's codes changed, 2026-11-17: 83ED to 88B7."""
    pump.renumber("88B7")

    found = await find_pages(client, pump.site[webif.OVERVIEW])

    assert found[MODULATION_PAGE] == MODULATION_PATH.replace(TOKEN, "88B7")


async def test_a_heat_pump_menu_without_it_leaves_it_out(pump, client):
    pump.site[PUMP_MENU_PATH] = pump.site[PUMP_MENU_PATH].replace(
        "Modulation", "Leistung"
    )

    assert MODULATION_PAGE not in await find_pages(client)


async def test_the_dialog_keeps_it_on_an_evoblock(hass, pump):
    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert visited.model == EVOBLOCK
    assert visited.pages == WITH_MODULATION


async def test_a_wbb_neither_keeps_nor_reads_it(hass, pump):
    pump.site.update(SITE)

    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert MODULATION_PAGE not in visited.pages
    assert ("GET", MODULATION_PATH) not in pump.asked


def form_page(path=MODULATION_PATH):
    return Page(
        MODULATION_PAGE,
        path,
        frozenset({"Modulation"}),
        interval=None,
        form="Modulation",
    )


@pytest.mark.parametrize(
    ("selected", "read"), [(AUTOMATIC, "-1"), (50, "50"), (100, "100")]
)
def test_its_reading_is_the_option_selected(selected, read):
    assert form_page().read(modulation_form(selected)) == [("Modulation", read)]
    assert form_page().is_whole(modulation_form(selected))


@pytest.mark.parametrize(
    "text",
    [
        main_menus(),
        modulation_form(50).replace('name="type"', 'name="other"'),
        limit_leaf(60),
    ],
    ids=["served half", "its form lacking a field", "another setting's form"],
)
def test_a_page_without_its_own_form_is_not_whole(text):
    """Another setting's form in its place must not pass for its value."""
    assert form_page().read(text) == []
    assert not form_page().is_whole(text)


def test_its_page_is_named_by_its_menu():
    assert PAGE_MENUS[MODULATION_PAGE] == "Wärmepumpe › Modulation"


def _web_entry(hass, pump, pages=WITH_MODULATION):
    web = _entries(hass, pump)
    hass.config_entries.async_update_entry(
        web, data={**web.data, CONF.MODEL: EVOBLOCK, CONF.PAGES: pages}
    )
    return web


def _select(hass):
    return er.async_get(hass).async_get_entity_id(
        "select", CONST.DOMAIN, f"{CONST.DEF_PREFIX}webif_modulation"
    )


async def _choose(hass, option):
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": _select(hass), "option": option},
        blocking=True,
    )


@pytest.mark.e2e
@pytest.mark.timeout(120)
async def test_an_evoblock_entry_shows_it_as_a_choice(hass, pump):
    await _start(hass, _web_entry(hass, pump))

    state = hass.states.get(_select(hass))
    assert state.state == "50"
    assert state.attributes["options"] == [
        "auto",
        *(str(percent) for percent in range(50, 101, 5)),
    ]


@pytest.mark.e2e
@pytest.mark.timeout(120)
async def test_an_entry_without_it_gets_no_choice(hass, pump):
    await _start(hass, _web_entry(hass, pump, ALL_PAGES))

    assert _select(hass) is None


@pytest.mark.e2e
@pytest.mark.timeout(120)
@pytest.mark.parametrize(
    ("before", "option", "sent"),
    [(50, "auto", "-1"), (AUTOMATIC, "50", "50"), (50, "85", "85")],
)
async def test_it_is_set_from_home_assistant(hass, pump, quick, before, option, sent):
    """Its form, one save, its form again: the page it is polled at."""
    show(pump, before)
    await _start(hass, _web_entry(hass, pump))
    pump.asked.clear()

    await _choose(hass, option)

    assert [form["value"] for form in pump.saved] == [sent]
    assert pump.saved[0]["stack"] == f"{PUMP_MENU},{MODULATION}"
    assert [path for _, path in pump.asked] == [
        MODULATION_PATH,
        "/pro_save.html",
        MODULATION_PATH,
    ]
    assert hass.states.get(_select(hass)).state == option


@pytest.mark.e2e
@pytest.mark.timeout(120)
async def test_the_option_shown_already_sends_nothing(hass, pump, quick):
    await _start(hass, _web_entry(hass, pump))

    await _choose(hass, "50")

    assert pump.saved == []


@pytest.mark.e2e
@pytest.mark.timeout(120)
async def test_an_option_the_form_does_not_offer_is_not_sent(hass, pump, quick):
    """Another firmware's list: the form is read before each save."""
    with_modulation(pump, 50, offered=(AUTOMATIC, 50, 60, 70))
    await _start(hass, _web_entry(hass, pump))

    with pytest.raises(HomeAssistantError) as raised:
        await _choose(hass, "55")

    assert raised.value.translation_key == "webif_write_form_mismatch"
    assert raised.value.translation_placeholders["setting"] == (
        "Wärmepumpe › Modulation"
    )
    assert pump.saved == []


@pytest.mark.e2e
@pytest.mark.timeout(120)
@pytest.mark.parametrize(
    "form",
    [limit_leaf(60), modulation_form(50).replace('"para_list"', '"para_text"')],
    ids=["another setting's form", "a form of another type"],
)
async def test_a_form_not_its_own_is_not_sent(hass, pump, quick, form):
    """Served in its place after the entry started: its value would be
    written to another setting, or as another kind of value."""
    await _start(hass, _web_entry(hass, pump))
    pump.site[MODULATION_PATH] = form

    with pytest.raises(HomeAssistantError):
        await _choose(hass, "85")

    assert pump.saved == []


@pytest.mark.e2e
@pytest.mark.timeout(120)
@pytest.mark.parametrize(("option", "target"), [("auto", "Automatik"), ("85", "85 %")])
async def test_another_option_read_back_is_named(hass, pump, quick, option, target):
    """Saved, and the form still shows 50: both are named as the menu shows
    them."""
    await _start(hass, _web_entry(hass, pump))
    pump.saves_elsewhere = lambda form: pump.current(PUMP_MENU_PATH)

    with pytest.raises(HomeAssistantError) as raised:
        await _choose(hass, option)

    assert raised.value.translation_key == "webif_write_other_value"
    assert raised.value.translation_placeholders == {
        "setting": "Wärmepumpe › Modulation",
        "shown": "50 %",
        "target": target,
    }
    assert hass.states.get(_select(hass)).state == "50"


@pytest.mark.e2e
@pytest.mark.timeout(120)
async def test_a_wbb_entry_gets_no_choice(hass, pump):
    """Its pages never hold the modulation; should they, it is not offered."""
    web = _web_entry(hass, pump)
    hass.config_entries.async_update_entry(web, data={**web.data, CONF.MODEL: WBB})
    pump.site.update(SITE)
    await _start(hass, web)

    assert _select(hass) is None


def test_it_is_polled_on_the_settings_interval():
    polled = {
        page.key: page
        for page in polled_pages(
            WITH_MODULATION,
            {CONST.OPTION_WEBIF_SETTINGS_INTERVAL: 30},
            REQUIRED_TITLES[EVOBLOCK],
            ENGLISH_TITLES[EVOBLOCK],
            OPTIONAL_TITLES[EVOBLOCK],
        )
    }

    assert polled[MODULATION_PAGE].form == "Modulation"
    assert polled[MODULATION_PAGE].interval.total_seconds() == 30 * 60
