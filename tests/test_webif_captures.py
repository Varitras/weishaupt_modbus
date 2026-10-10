"""The catalogue against files tools/webif_capture.py wrote on a real controller.

The other tests read pages built from the catalogue itself, so a title the
controller shows otherwise, in German or in English, passes them all and
fails only live. Opt in by pointing WEISHAUPT_WEBIF_CAPTURES at a folder of
such files; without it the test is skipped. The files never enter the
repository: they hold a real controller's readings.
"""

import json
import os
import pathlib

import pytest

from custom_components.weishaupt_modbus.webif import discovery
from custom_components.weishaupt_modbus.webif.discovery import (
    HEAT_PUMP_PAGE,
    HEATING_PAGE,
    STATISTICS_PAGE,
)
from custom_components.weishaupt_modbus.webif_coordinator import polled_pages
from custom_components.weishaupt_modbus.webif_sensor import (
    ENGLISH_TITLES,
    READ_VALUES,
    REQUIRED_TITLES,
    shown_in_unknown_units,
)

from .webif_stand_in import (
    HEATING,
    HEATING_PATH,
    PUMP_MENU,
    column,
    limit_segment,
    link,
    value,
)

CAPTURES = os.environ.get("WEISHAUPT_WEBIF_CAPTURES")
pytestmark = pytest.mark.skipif(
    not CAPTURES, reason="set WEISHAUPT_WEBIF_CAPTURES to a folder of capture files"
)
CAPTURED = (
    sorted(pathlib.Path(CAPTURES).glob("webif_titles_*.json")) if CAPTURES else []
)
PATHS = {
    HEAT_PUMP_PAGE: "/heat_pump",
    STATISTICS_PAGE: "/statistics",
    HEATING_PAGE: HEATING_PATH,
}
# The menu pages a capture holds, and the entries the pages are found by.
MENUS = {
    "overview": {discovery.INFO, discovery.HEAT_PUMP},
    "info": {discovery.HEAT_PUMP, discovery.STATISTICS},
    "heat_pump_menu": {discovery.HEATING},
}


def _page(capture: pathlib.Path, key: str) -> dict:
    captured = json.loads(capture.read_text(encoding="utf-8"))["pages"]
    return next(page for page in captured if page["page"] == key)


def _titles(capture: pathlib.Path, key: str) -> list[str]:
    """The titles a captured page shows, in their order there.

    Older captures list a menu's titles as entries without a value.
    """
    page = _page(capture, key)
    if "titles" in page:
        return page["titles"]
    return [title for title, _ in page["entries"]]


def _as_served(key: str, entries: list[list[str]]) -> str:
    """The captured titles and values as the controller's page carries them."""
    if key != HEATING_PAGE:
        return column("".join(value(title, shown) for title, shown in entries))
    # Any segment one level below the heating menu; the limit's has the form.
    return column(
        "".join(
            link([PUMP_MENU, HEATING, limit_segment(index)], title, shown)
            for index, (title, shown) in enumerate(entries)
        )
    )


def test_the_folder_holds_captures():
    """Pointed at a folder without any, every other test here was skipped and
    the run passed."""
    assert CAPTURED, f"no webif_titles_*.json in {CAPTURES}"


@pytest.mark.parametrize("capture", CAPTURED, ids=lambda capture: capture.stem)
@pytest.mark.parametrize("key", list(PATHS))
def test_the_catalogue_reads_the_captured_page(capture, key):
    """Every value read is on the page once, in the unit its sensor expects."""
    page = next(
        page
        for page in polled_pages(PATHS, {}, REQUIRED_TITLES, ENGLISH_TITLES)
        if page.key == key
    )
    text = _as_served(key, _page(capture, key)["entries"])

    assert page.is_whole(text), f"lacks {sorted(page.missing(text))}"
    assert not shown_in_unknown_units(key, page.read(text))


def _unpaired(key: str, pairs: dict[str, str]) -> dict[str, set[int]]:
    """The pairs of a German and an English title that are none, by the rows
    they stand in over all captures.

    Both languages list the same rows in the same order, so a true pair
    stands in one row. One standing in two rows, or whose English title no
    English capture shows, is not a pair. A capture without any of the
    German titles counts as English.
    """
    differing = {german for german, english in pairs.items() if german != english}
    rows: dict[str, set[int]] = {german: set() for german in pairs}
    shown_in_english: set[str] = set()
    english_captures = 0
    for capture in CAPTURED:
        titles = _titles(capture, key)
        if not differing & set(titles):
            english_captures += 1
            shown_in_english.update(titles)
        for german, english in pairs.items():
            rows[german].update(
                index
                for index, title in enumerate(titles)
                if title in (german, english)
            )
    if english_captures in (0, len(CAPTURED)):
        pytest.skip("pairs need a capture in German and one in English")
    return {
        german: row
        for german, row in rows.items()
        if len(row) != 1 or pairs[german] not in shown_in_english
    }


@pytest.mark.parametrize("key", list(PATHS))
def test_each_english_title_stands_where_its_german_one_does(key):
    """An English title paired with another value of the same unit passed
    every other test, and its sensor would show that value."""
    pairs = {
        sensor.title: sensor.english_title
        for sensor in READ_VALUES
        if sensor.page == key
    }

    assert _unpaired(key, pairs) == {}


@pytest.mark.parametrize("key", list(MENUS))
def test_each_english_menu_entry_stands_where_its_german_one_does(key):
    """The menu entries the pages are found by, in both languages."""
    pairs = {
        german: english
        for english, german in discovery.ENGLISH_MENU_TITLES.items()
        if german in MENUS[key]
    }

    assert set(pairs) == MENUS[key]
    assert _unpaired(key, pairs) == {}
