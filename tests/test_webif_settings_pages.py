"""The settings pages of each heating circuit and of the hot water.

User wish, 2026-11-17: each circuit's minimum flow temperature, set from Home
Assistant instead of at the pump on windy days, and beside it the maximum,
the demand increase, the SG Ready raise and the hot water's system operating
mode, which Modbus does not carry. Seen on a WEB 13/20 set to German alone.
"""

import asyncio
import inspect

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weishaupt_modbus import webif_coordinator
from custom_components.weishaupt_modbus.const import CONF, CONST
from custom_components.weishaupt_modbus.webif import client as webif, setting
from custom_components.weishaupt_modbus.webif.discovery import (
    HEAT_PUMP_PAGE,
    HOT_WATER_SETTINGS_PAGE,
    circuit_settings_page,
    find_menus,
    find_settings_pages,
    settings_menus,
)
from custom_components.weishaupt_modbus.webif.setting import (
    MINIMUM_TEMPERATURE,
    MODULATION,
    POWER_LIMIT,
    Setting,
    shown_tenths,
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
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .test_webif_evoblock import ROWS, _state, heat_pump_page, shown
from .test_webif_optional_pages import OPTIONAL, info_menu, values_page
from .test_webif_setup import (
    PAGES,
    SITE,
    _enable_custom_integrations,  # noqa: F401
    _entries,
    _start,
)
from .webif_stand_in import (
    INFO,
    PASSWORD,
    STACK,
    USER,
    StandInPump,
    column,
    link,
    main_menus,
    menu_site,
    serving,
)

# Made-up segments shaped like the controller's, its device token 0A0B.
SECOND_MENU = "33000001000000000080000A0B010002000301"
THIRD_MENU = "34000001000000000080000A0B010002000301"
HOT_WATER_MENU = "46000001000000000080000A0B010002000301"
MENUS = {2: SECOND_MENU, 3: THIRD_MENU}
NAMES = {2: "FBH", 3: "RADIATOREN"}


def settings_segment(menu):
    return menu[:2] + "00320000000000008000" + "0A0B020003000401"


def entry_segment(menu, row, shown_value):
    """A setting's own segment: the controller writes its value into it."""
    code = sum(map(ord, shown_value)) % 0x1000000
    return f"{menu[:2]}0032{row:02X}{code:010X}40000A0B030011010401"


SETTINGS = {
    **{
        circuit_settings_page(circuit): STACK + f"{menu},{settings_segment(menu)}"
        for circuit, menu in MENUS.items()
    },
    HOT_WATER_SETTINGS_PAGE: STACK
    + f"{HOT_WATER_MENU},{settings_segment(HOT_WATER_MENU)}",
}
ALL_PAGES = {**PAGES, **OPTIONAL, **SETTINGS}


def overview():
    """The main menus with the circuits and the hot water beside Info."""
    return main_menus() + column(
        link([SECOND_MENU], "Heizkreis 2", NAMES[2])
        + link([THIRD_MENU], "Heizkreis 3", NAMES[3])
        + link([HOT_WATER_MENU], "Warmwasser")
    )


def menu_page(menu, *titles):
    """A circuit's or the hot water's menu, its entries one level down."""
    return overview() + column(
        "".join(
            link(
                [menu, f"{menu[:2]}00{index:02X}00000000000080000A0B020003000401"],
                title,
            )
            if title != "Einstellungen"
            else link([menu, settings_segment(menu)], title)
            for index, title in enumerate(titles, start=0x30)
        )
    )


def settings_page(menu, rows, values=None):
    """A settings page: each setting a link that shows its value."""
    values = values or {}
    base = [menu, settings_segment(menu)]
    return overview() + column(
        "".join(
            link(
                [*base, entry_segment(menu, row, values.get(title, shown(unit)))],
                title,
                values.get(title, shown(unit)),
            )
            for row, (title, unit) in enumerate(rows)
        )
    )


def minimum_leaf(menu, rows, values, offered=range(100, 451, 5)):
    """The minimum's own page: the one form that saves it."""
    tenths = shown_tenths(values["Minimaltemperatur"])
    segment = entry_segment(
        menu,
        [title for title, _ in rows].index("Minimaltemperatur"),
        values["Minimaltemperatur"],
    )
    options = "".join(
        f'<option value="{option}"{" selected" if option == tenths else ""}>\n'
        f"{option / 10:.1f}</option>\n"
        for option in offered
    )
    return overview() + (
        '<form action="pro_save.html" method="POST">'
        f'<input type="hidden" name="id" value="{segment[:8]}01{segment[10:]}">\n'
        '<input type="hidden" name="stack" '
        f'value="{menu},{settings_segment(menu)},{segment}">\n'
        '<input type="hidden" name="type" value="para_list">'
        '<div class="form-group"><select class="form-control" name="value">\n'
        f"{options}</select></div>\n"
        '<button type="submit" class="btn btn-success">Speichern</button></form>\n'
    )


class Circuit:
    """A circuit's settings as the stand-in shows them; a save moves the minimum."""

    def __init__(self, pump, circuit, minimum="20.0 °C", maximum="45.0 °C"):
        self.pump = pump
        self.menu = MENUS[circuit]
        self.rows = ROWS[circuit_settings_page(circuit)]
        # As both circuits showed it: no raise set.
        self.values = {
            "Minimaltemperatur": minimum,
            "Maximaltemperatur": maximum,
            "SG Ready Anhebung": "Aus",
        }
        self.show()

    def show(self):
        path = SETTINGS[circuit_settings_page(self.circuit_number)]
        for old in [p for p in self.pump.site if p.startswith(path + ",")]:
            del self.pump.site[old]
        self.pump.site[path] = settings_page(self.menu, self.rows, self.values)
        segment = entry_segment(
            self.menu,
            [title for title, _ in self.rows].index("Minimaltemperatur"),
            self.values["Minimaltemperatur"],
        )
        self.pump.site[f"{path},{segment}"] = minimum_leaf(
            self.menu, self.rows, self.values
        )

    @property
    def circuit_number(self):
        return next(number for number, menu in MENUS.items() if menu == self.menu)

    def save(self, form):
        self.values["Minimaltemperatur"] = f"{int(form['value']) / 10:.1f} °C"
        self.show()
        return SETTINGS[circuit_settings_page(self.circuit_number)]


def evoblock_site(pump):
    pump.site = {
        **menu_site(),
        **SITE,
        webif.OVERVIEW: overview(),
        STACK + INFO: info_menu(),
        PAGES[HEAT_PUMP_PAGE]: heat_pump_page(ROWS[HEAT_PUMP_PAGE]),
        OPTIONAL["heating_circuit"]: values_page(ROWS["heating_circuit"]),
        OPTIONAL["second_generator"]: values_page(ROWS["second_generator"]),
        STACK + SECOND_MENU: menu_page(SECOND_MENU, "Betriebsart", "Einstellungen"),
        STACK + THIRD_MENU: menu_page(THIRD_MENU, "Betriebsart", "Einstellungen"),
        STACK + HOT_WATER_MENU: menu_page(HOT_WATER_MENU, "Einstellungen", "Reset"),
        SETTINGS[HOT_WATER_SETTINGS_PAGE]: settings_page(
            HOT_WATER_MENU,
            ROWS[HOT_WATER_SETTINGS_PAGE],
            {"Systembetriebsart": "Parallel"},
        ),
    }
    pump.circuits = {2: Circuit(pump, 2), 3: Circuit(pump, 3, "33.0 °C", "55.0 °C")}

    def saves(form):
        menu = form["stack"].split(",")[0]
        number = next(n for n, m in MENUS.items() if m == menu)
        return pump.circuits[number].save(form)

    pump.saves_elsewhere = saves


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(webif_coordinator, "DEBOUNCE_SECONDS", 0.05)


@pytest.fixture
async def pump(socket_enabled, monkeypatch):
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    stand_in = StandInPump()
    evoblock_site(stand_in)
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


async def settings_found(client, *, last=True):
    """The settings pages, searched from the menus as the dialog does."""
    return await find_settings_pages(
        client, settings_menus(await find_menus(client)), last=last
    )


async def test_the_settings_of_each_circuit_info_lists_and_of_the_hot_water_are_found(
    client,
):
    assert await settings_found(client) == SETTINGS


async def test_a_menu_without_its_settings_leaves_only_that_page_out(pump, client):
    """Another model's circuit, or a hot water menu shown otherwise: its page
    goes, nothing fails."""
    pump.site[STACK + THIRD_MENU] = menu_page(THIRD_MENU, "Betriebsart", "Reset")
    pump.site[webif.OVERVIEW] = main_menus() + column(
        link([SECOND_MENU], "Heizkreis 2", NAMES[2])
        + link([THIRD_MENU], "Heizkreis 3", NAMES[3])
    )

    found = await settings_found(client)

    assert circuit_settings_page(3) not in found
    assert HOT_WATER_SETTINGS_PAGE not in found
    assert found[circuit_settings_page(2)] == SETTINGS[circuit_settings_page(2)]


async def test_the_dialog_leaves_out_a_menu_without_its_settings_unsearched(hass, pump):
    """Another model's menu: a second search would only add load."""
    pump.site[STACK + THIRD_MENU] = menu_page(THIRD_MENU, "Betriebsart", "Reset")

    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert circuit_settings_page(3) not in visited.pages
    assert circuit_settings_page(2) in visited.pages
    assert pump.asked.count(("GET", STACK + THIRD_MENU)) == 2


async def test_a_circuit_info_does_not_list_is_not_looked_for(pump, client):
    """The Evoblock's first circuit is the heat pump itself: its menu holds
    a switch, no settings page."""
    pump.site[STACK + INFO] = info_menu().replace("Heizkreis 3", "Heizkreis 9")

    await settings_found(client)

    assert ("GET", STACK + THIRD_MENU) not in pump.asked


async def test_the_dialog_keeps_the_settings_pages_of_an_evoblock(hass, pump):
    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert visited.model == EVOBLOCK
    assert visited.pages == ALL_PAGES


async def test_a_wbb_neither_keeps_nor_reads_them(hass, pump):
    pump.site.update(SITE)

    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert visited.model == WBB
    # Asked what it was before they were read: not even their menus.
    menus = [STACK + menu for menu in (*MENUS.values(), HOT_WATER_MENU)]
    for path in (*SETTINGS.values(), *menus):
        assert ("GET", path) not in pump.asked


async def test_a_settings_page_lacking_a_row_costs_only_itself(hass, pump):
    pump.circuits[3].rows = [
        row for row in ROWS[circuit_settings_page(3)] if row[0] != "Maximaltemperatur"
    ]
    pump.circuits[3].show()

    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert circuit_settings_page(3) not in visited.pages
    assert circuit_settings_page(2) in visited.pages


THIRD_SETTINGS = SETTINGS[circuit_settings_page(3)]
# The overview, the main menus alone: what a menu or a settings page served
# with nothing of its own level shows.
HALF = overview()


def kept_but(*keys):
    """Every page the dialog keeps on this Evoblock but those of `keys`."""
    return {key: path for key, path in ALL_PAGES.items() if key not in keys}


@pytest.mark.parametrize(
    "unclear",
    ["served half", "a value blank"],
)
async def test_a_settings_page_unclear_in_both_searches_costs_only_itself(
    hass, pump, unclear
):
    """Served half, or with a value blank, also in the second search: the
    page goes, the dialog does not fail."""
    if unclear == "served half":
        pump.site[THIRD_SETTINGS] = HALF
    else:
        pump.circuits[3].values["Maximaltemperatur"] = ""
        pump.circuits[3].show()

    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert visited.pages == kept_but(circuit_settings_page(3))
    # Twice in each search.
    assert pump.asked.count(("GET", THIRD_SETTINGS)) == 4


async def test_a_settings_page_the_second_search_brings_whole_is_kept(hass, pump):
    """A hiccup of the controller costs a second search, not the page."""
    whole = pump.site.pop(THIRD_SETTINGS)
    pump.pages = [HALF, HALF, whole]

    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert visited.pages == ALL_PAGES
    assert pump.asked.count(("GET", THIRD_SETTINGS)) == 3


@pytest.mark.parametrize(
    "menu",
    [
        HALF,
        menu_page(THIRD_MENU, "Betriebsart", "Einstellungen", "Einstellungen"),
    ],
    ids=["served half", "its settings twice"],
)
async def test_a_settings_menu_unclear_in_both_searches_costs_only_its_page(
    hass, pump, menu
):
    """As the required menus, an unclear one is searched again; then its
    page goes, the dialog does not fail."""
    pump.site[STACK + THIRD_MENU] = menu

    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert visited.pages == kept_but(circuit_settings_page(3))
    assert pump.asked.count(("GET", STACK + THIRD_MENU)) == 4


async def test_a_settings_menu_shown_twice_once_is_searched_again(hass, pump):
    """Its settings shown twice in the first search, once in the second."""
    whole = pump.site.pop(STACK + THIRD_MENU)
    doubled = menu_page(THIRD_MENU, "Betriebsart", "Einstellungen", "Einstellungen")
    pump.pages = [doubled, doubled, whole]

    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert visited.pages == ALL_PAGES


# Heizkreis 3 linked to the menu of Heizkreis 2.
SHARED_OVERVIEW = main_menus() + column(
    link([SECOND_MENU], "Heizkreis 2", NAMES[2])
    + link([SECOND_MENU], "Heizkreis 3", NAMES[3])
    + link([HOT_WATER_MENU], "Warmwasser")
)


async def test_two_circuits_at_one_settings_page_are_both_left_out(pump, client):
    """Which circuit a value written there reaches cannot be told."""
    pump.site[webif.OVERVIEW] = SHARED_OVERVIEW

    assert await settings_found(client) == {
        HOT_WATER_SETTINGS_PAGE: SETTINGS[HOT_WATER_SETTINGS_PAGE]
    }


async def test_the_dialog_leaves_out_two_circuits_at_one_settings_page(hass, pump):
    pump.site[webif.OVERVIEW] = SHARED_OVERVIEW

    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert visited.pages == kept_but(circuit_settings_page(2), circuit_settings_page(3))


def test_a_settings_page_is_a_menu_on_the_settings_interval():
    polled = {
        page.key: page
        for page in polled_pages(
            ALL_PAGES,
            {CONST.OPTION_WEBIF_SETTINGS_INTERVAL: 30},
            REQUIRED_TITLES[EVOBLOCK],
            ENGLISH_TITLES[EVOBLOCK],
            OPTIONAL_TITLES[EVOBLOCK],
        )
    }

    for key in SETTINGS:
        assert polled[key].menu
        assert polled[key].interval.total_seconds() == 30 * 60


def test_only_an_evoblock_reads_them():
    for key in SETTINGS:
        assert key not in REQUIRED_TITLES[WBB]
    assert REQUIRED_TITLES[EVOBLOCK][circuit_settings_page(2)] == {
        "Minimaltemperatur",
        "Maximaltemperatur",
        "Anforderungsüberhöhung",
        "SG Ready Anhebung",
    }
    assert REQUIRED_TITLES[EVOBLOCK][HOT_WATER_SETTINGS_PAGE] == {"Systembetriebsart"}


@pytest.mark.parametrize(
    ("shown_value", "tenths"),
    [
        ("33.0 °C", 330),
        ("33.5 °C", 335),
        ("-2.5 °C", -25),
        ("33.0 K", None),
        ("Aus", None),
        ("33.25 °C", None),
    ],
)
def test_a_temperature_is_the_option_of_its_tenths(shown_value, tenths):
    """The form offers 33.5 °C as "335"."""
    assert shown_tenths(shown_value) == tenths


def test_only_the_power_limit_the_minimum_temperature_and_the_modulation_are_written():
    """Spec: nothing else can be written. A Setting is what write_setting
    takes; the module defines these three and no other. The modulation, an
    Evoblock's, user wish 2026-11-18."""
    defined = {
        name
        for name, value in inspect.getmembers(setting)
        if isinstance(value, Setting)
    }

    assert defined == {"POWER_LIMIT", "MINIMUM_TEMPERATURE", "MODULATION"}
    assert POWER_LIMIT.titles[0] == "Leistungsbegrenzung"
    assert MINIMUM_TEMPERATURE.titles == ("Minimaltemperatur",)
    assert MODULATION.titles == ("Modulation",)
    assert [s.form_page for s in (POWER_LIMIT, MINIMUM_TEMPERATURE, MODULATION)] == [
        False,
        False,
        True,
    ]


def _web_entry(hass, pump):
    web = _entries(hass, pump)
    hass.config_entries.async_update_entry(
        web, data={**web.data, CONF.MODEL: EVOBLOCK, CONF.PAGES: ALL_PAGES}
    )
    return web


def _number(hass, circuit):
    return er.async_get(hass).async_get_entity_id(
        "number", CONST.DOMAIN, f"{CONST.DEF_PREFIX}webif_hk{circuit}_minimaltemperatur"
    )


@pytest.mark.e2e
@pytest.mark.timeout(120)
async def test_an_evoblock_entry_reads_each_circuits_settings(hass, pump):
    await _start(hass, _web_entry(hass, pump))

    assert _state(hass, "hk3_maximaltemperatur") == "55.0"
    assert _state(hass, "hk2_maximaltemperatur") == "45.0"
    assert _state(hass, "hk2_anforderungsueberhoehung") == "12.0"
    # "Aus": no raise set.
    assert _state(hass, "hk2_sg_ready_anhebung") == "0.0"
    assert _state(hass, "ww_systembetriebsart") == "Parallel"
    number = hass.states.get(_number(hass, 3))
    assert number.state == "33.0"
    assert number.attributes["max"] == 55.0
    assert number.attributes["step"] == 0.5
    assert _number(hass, 1) is None


@pytest.mark.e2e
@pytest.mark.timeout(120)
async def test_an_entry_made_before_them_gets_no_settings_entities(hass, pump):
    web = _entries(hass, pump)
    hass.config_entries.async_update_entry(web, data={**web.data, CONF.MODEL: EVOBLOCK})
    await _start(hass, web)

    assert _number(hass, 2) is None
    assert _state(hass, "hk2_maximaltemperatur") is None


@pytest.mark.e2e
@pytest.mark.timeout(120)
async def test_the_minimum_temperature_is_set_from_home_assistant(hass, pump, quick):
    """The settings page, the minimum's own page, one save, the settings page
    again, as for the power limit."""
    await _start(hass, _web_entry(hass, pump))
    pump.asked.clear()

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _number(hass, 3), "value": 33.5},
        blocking=True,
    )

    assert [form["value"] for form in pump.saved] == ["335"]
    assert hass.states.get(_number(hass, 3)).state == "33.5"
    assert pump.circuits[2].values["Minimaltemperatur"] == "20.0 °C"
    page = SETTINGS[circuit_settings_page(3)]
    paths = [path for _, path in pump.asked]
    assert len(paths) == 4
    assert (paths[0], paths[2], paths[3]) == (page, "/pro_save.html", page)
    assert paths[1].startswith(page + ",")


@pytest.mark.e2e
@pytest.mark.timeout(120)
async def test_two_circuits_set_within_the_quiet_time_each_get_their_value(
    hass, pump, quick
):
    """Each setting has its own quiet time: the two make two writes, neither
    the last value of the other."""
    await _start(hass, _web_entry(hass, pump))

    await asyncio.gather(
        *(
            hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": _number(hass, circuit), "value": target},
                blocking=True,
            )
            for circuit, target in ((3, 33.5), (2, 21.0))
        )
    )

    assert sorted(
        (form["stack"].split(",")[0], form["value"]) for form in pump.saved
    ) == sorted([(THIRD_MENU, "335"), (SECOND_MENU, "210")])
    assert pump.circuits[3].values["Minimaltemperatur"] == "33.5 °C"
    assert pump.circuits[2].values["Minimaltemperatur"] == "21.0 °C"
    assert hass.states.get(_number(hass, 3)).state == "33.5"
    assert hass.states.get(_number(hass, 2)).state == "21.0"


@pytest.mark.e2e
@pytest.mark.timeout(120)
async def test_a_minimum_off_the_half_degree_steps_is_refused_unsent(hass, pump, quick):
    await _start(hass, _web_entry(hass, pump))

    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": _number(hass, 3), "value": 33.3},
            blocking=True,
        )

    assert raised.value.translation_key == "webif_minimum_temperature_step"
    assert pump.saved == []


async def test_the_options_offer_one_interval_for_the_settings_pages(hass):
    entry = MockConfigEntry(
        domain=CONST.DOMAIN,
        data={CONF.KIND: CONST.WEB_INTERFACE, CONF.PAGES: ALL_PAGES},
    )
    entry.add_to_hass(hass)

    form = await hass.config_entries.options.async_init(entry.entry_id)

    fields = [str(field) for field in form["data_schema"].schema]
    assert fields.count(CONST.OPTION_WEBIF_SETTINGS_INTERVAL) == 1
