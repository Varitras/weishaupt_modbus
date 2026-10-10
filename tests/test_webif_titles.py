"""The catalogue's titles against those a real controller showed.

webif_titles.json holds the titles of the pages and menus the integration
reads, German and English side by side as one controller listed them
(tools/webif_capture.py on a WBB, 2026-10-04 and 2026-10-10), with the unit
each row showed and no value. Both languages list the same rows in the same
order. test_webif_captures.py checks the file against the captures.
"""

import json
from pathlib import Path

import pytest

from custom_components.weishaupt_modbus.webif.discovery import ENGLISH_MENU_TITLES
from custom_components.weishaupt_modbus.webif_sensor import ENGLISH_TITLES, READ_VALUES

TITLES = json.loads(
    (Path(__file__).parent / "webif_titles.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("sensor", READ_VALUES, ids=lambda sensor: sensor.key)
def test_each_value_is_read_under_the_titles_and_unit_the_controller_shows(sensor):
    """An English title paired with another value of the same unit passed
    every test CI runs, and its sensor would show that value."""
    rows = {
        german: (english, unit)
        for german, english, unit in TITLES["pages"][sensor.page]
    }

    assert rows[sensor.title] == (sensor.english_title, sensor.shown_unit)


@pytest.mark.parametrize(("english", "german"), ENGLISH_MENU_TITLES.items())
def test_each_menu_entry_is_found_under_the_title_the_controller_shows(english, german):
    pairs = {tuple(pair) for rows in TITLES["menus"].values() for pair in rows}

    assert (german, english) in pairs


@pytest.mark.parametrize("key", list(ENGLISH_TITLES))
def test_no_english_title_is_the_german_title_of_another_row(key):
    """Every title a page shows is mapped, read or not: an English title
    equal to another row's German one would read that row as the value."""
    shown = {german for german, _, _ in TITLES["pages"][key]}

    assert {
        english: german
        for english, german in ENGLISH_TITLES[key].items()
        if english in shown - {german}
    } == {}


@pytest.mark.parametrize("menu", list(TITLES["menus"]))
def test_no_english_menu_title_is_the_german_title_of_another_entry(menu):
    shown = {german for german, _ in TITLES["menus"][menu]}

    assert {
        english: german
        for english, german in ENGLISH_MENU_TITLES.items()
        if english in shown - {german}
    } == {}
