"""An Evoblock's pages, by the titles and units a WEB 13/20 showed on 2026-11-05."""

import json
from pathlib import Path

import pytest

from custom_components.weishaupt_modbus.const import CONF, CONST
from custom_components.weishaupt_modbus.webif import client as webif
from custom_components.weishaupt_modbus.webif.discovery import (
    HEAT_PUMP_PAGE,
    HEATING_PAGE,
    STATISTICS_PAGE,
)
from custom_components.weishaupt_modbus.webif_sensor import (
    ENGLISH_TITLES,
    EVOBLOCK,
    VALUES,
    WBB,
    entry_model,
    model_shown,
    shown_in_unknown_units,
)
from custom_components.weishaupt_modbus.webif_visit import (
    MissingTitles,
    UnknownUnits,
    read_web_interface,
)
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory, UnitOfFrequency, UnitOfPressure
from homeassistant.helpers import entity_registry as er

from .test_webif_setup import (
    PAGES,
    SHOWN,
    SITE,
    _enable_custom_integrations,  # noqa: F401
    _entries,
    _start,
)
from .webif_stand_in import (
    PASSWORD,
    USER,
    StandInPump,
    column,
    menu_site,
    serving,
    value,
)

ROWS = json.loads(
    (Path(__file__).parent / "evoblock_titles.json").read_text(encoding="utf-8")
)
# The capture was made at a standstill: these showed a word, not a number.
WORD = "Aus"
SAMPLE_NUMBER = "12.0"


def shown(unit):
    return WORD if unit is None else f"{SAMPLE_NUMBER} {unit}".strip()


def heat_pump_page(rows):
    return column("".join(value(title, shown(unit)) for title, unit in rows))


def without(*titles):
    return [row for row in ROWS[HEAT_PUMP_PAGE] if row[0] not in titles]


@pytest.fixture
async def pump(socket_enabled, monkeypatch):
    """The menus, and the pages an Evoblock shows."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    stand_in = StandInPump()
    stand_in.site = {
        **menu_site(),
        **SITE,
        PAGES[HEAT_PUMP_PAGE]: heat_pump_page(ROWS[HEAT_PUMP_PAGE]),
    }
    async with serving(stand_in) as served:
        yield served


async def _evoblock(hass, pump):
    """A web interface entry the dialog made for an Evoblock, started."""
    web = _entries(hass, pump)
    hass.config_entries.async_update_entry(web, data={**web.data, CONF.MODEL: EVOBLOCK})
    return await _start(hass, web)


def _entry(hass, key):
    registry = er.async_get(hass)
    unique_id = f"{CONST.DEF_PREFIX}webif_{key}"
    return registry.async_get(
        registry.async_get_entity_id("sensor", CONST.DOMAIN, unique_id)
    )


def _state(hass, key):
    unique_id = f"{CONST.DEF_PREFIX}webif_{key}"
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", CONST.DOMAIN, unique_id
    )
    return None if entity_id is None else hass.states.get(entity_id).state


async def test_the_dialog_accepts_an_evoblock(hass, pump):
    """It had refused one: its page lacks the EVI rows of a WBB."""
    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert visited.model == EVOBLOCK
    assert visited.pages[HEATING_PAGE] == PAGES[HEATING_PAGE]


async def test_an_answer_without_the_version_row_does_not_settle_the_family(hass, pump):
    """Broken first answers are common: judged by one, an Evoblock would be
    refused for lacking the rows of a WBB."""
    whole = pump.site.pop(PAGES[HEAT_PUMP_PAGE])
    pump.pages = [heat_pump_page(without("Version WEB CPU")), whole]

    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert visited.model == EVOBLOCK


async def test_a_wbb_stays_a_wbb(hass, pump):
    pump.site.update(SITE)

    visited = await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert visited.model == WBB


async def test_the_visit_opens_each_page_once(hass, pump):
    """The family comes from the answer the page is read for anyway: the
    controller is slow to answer, and each extra page is seconds of it."""
    await read_web_interface(hass, pump.host, USER, PASSWORD)

    for path in PAGES.values():
        assert pump.asked.count(("GET", path)) == 1


async def test_a_wbb_page_without_its_evi_rows_is_no_evoblock(hass, pump):
    """Told by the rows missing, a WBB page served short would become an
    Evoblock and lose its sensors for good."""
    rows = {
        title: shown
        for title, shown in SHOWN[HEAT_PUMP_PAGE].items()
        if "EVI" not in title
    }
    pump.site[PAGES[HEAT_PUMP_PAGE]] = column(
        "".join(value(title, shown) for title, shown in rows.items())
    )

    with pytest.raises(MissingTitles) as raised:
        await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert "EVI Sauggastemperatur" in raised.value.titles


async def test_the_compressor_in_rpm_is_an_unknown_unit_on_an_evoblock(hass, pump):
    rows = [
        [title, "rpm" if title == "Verdichter" else unit]
        for title, unit in ROWS[HEAT_PUMP_PAGE]
    ]
    pump.site[PAGES[HEAT_PUMP_PAGE]] = heat_pump_page(rows)

    with pytest.raises(UnknownUnits) as raised:
        await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert raised.value.titles == {"Verdichter"}


def test_the_compressor_in_hertz_is_an_unknown_unit_on_a_wbb():
    found = [("Verdichter", "12.0 Hz")]

    assert shown_in_unknown_units(HEAT_PUMP_PAGE, found, WBB) == {"Verdichter"}
    assert not shown_in_unknown_units(HEAT_PUMP_PAGE, found, EVOBLOCK)


@pytest.mark.parametrize("key", [STATISTICS_PAGE, HEATING_PAGE])
def test_the_other_pages_do_not_tell_the_family(key):
    """They read alike on both: only the heat pump page can say."""
    assert {value.title for value in VALUES[WBB] if value.page == key} == {
        value.title for value in VALUES[EVOBLOCK] if value.page == key
    }


def test_the_heat_pump_page_tells_the_family():
    assert model_shown(heat_pump_page(ROWS[HEAT_PUMP_PAGE])) == EVOBLOCK
    assert model_shown(SITE[PAGES[HEAT_PUMP_PAGE]]) == WBB
    assert model_shown("") == WBB


def test_an_entry_made_before_there_were_two_families_is_a_wbb():
    assert entry_model({}) == WBB
    assert entry_model({CONF.MODEL: EVOBLOCK}) == EVOBLOCK


async def test_an_evoblock_gets_the_sensors_its_page_can_fill(hass, pump):
    await _evoblock(hass, pump)

    assert _state(hass, "verdichter_frequenz") == "12.0"
    assert _state(hass, "wasserdruck") == "12.0"
    assert _state(hass, "waermetauscher_ag_austritt") == "12.0"
    assert _state(hass, "version_web_cpu") == "Aus"
    assert _state(hass, "evi_sauggastemperatur") is None
    assert _state(hass, "verdichter_drehzahl") is None


async def test_the_sensors_of_an_evoblock_show_what_they_are(hass, pump):
    await _evoblock(hass, pump)

    frequency = _entry(hass, "verdichter_frequenz")
    assert frequency.unit_of_measurement == UnitOfFrequency.HERTZ
    assert frequency.original_device_class is SensorDeviceClass.FREQUENCY
    assert _entry(hass, "wasserdruck").unit_of_measurement == UnitOfPressure.BAR
    version = _entry(hass, "version_web_cpu")
    assert version.entity_category is EntityCategory.DIAGNOSTIC


async def test_an_evoblock_keeps_the_power_limit(hass, pump):
    await _evoblock(hass, pump)

    assert er.async_get(hass).async_get_entity_id(
        "number", CONST.DOMAIN, f"{CONST.DEF_PREFIX}webif_leistungsbegrenzung_heizen"
    )


async def test_a_wbb_entry_gets_no_sensor_of_an_evoblock(hass, pump):
    pump.site.update(SITE)

    await _start(hass, _entries(hass, pump))

    assert _state(hass, "verdichter_drehzahl") == "2568.0"
    assert _state(hass, "verdichter_frequenz") is None
    assert _state(hass, "wasserdruck") is None


async def test_an_evoblock_page_without_a_row_of_its_own_is_refused(hass, pump):
    """Only the WBB's rows left out of the catalogue, a page served without
    the Evoblock's would pass for whole."""
    pump.site[PAGES[HEAT_PUMP_PAGE]] = heat_pump_page(without("Wasserdruck"))

    with pytest.raises(MissingTitles) as raised:
        await read_web_interface(hass, pump.host, USER, PASSWORD)

    assert raised.value.titles == {"Wasserdruck"}


def test_a_value_not_seen_in_english_gets_no_english_title():
    """An empty one would read a row without a title as that value."""
    assert not any("" in titles for titles in ENGLISH_TITLES[EVOBLOCK].values())
