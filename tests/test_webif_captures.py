"""The catalogue against files tools/webif_capture.py wrote on a real controller.

The other tests read pages built from the catalogue itself, so a title the
controller shows otherwise, in German or in English, passes them all and
fails only live. Opt in by pointing WEISHAUPT_WEBIF_CAPTURES at a folder of
such files; without it the test is skipped. The files never enter the
repository: they hold a real controller's readings. What CI checks the
catalogue against, webif_titles.json, is checked against them here.
"""

import json
import os
import pathlib

import pytest

from custom_components.weishaupt_modbus.webif import pages
from custom_components.weishaupt_modbus.webif.discovery import (
    HEAT_PUMP_PAGE,
    HEATING_PAGE,
    STATISTICS_PAGE,
)
from custom_components.weishaupt_modbus.webif_coordinator import polled_pages
from custom_components.weishaupt_modbus.webif_sensor import (
    ENGLISH_TITLES,
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
TITLES = json.loads(
    (pathlib.Path(__file__).parent / "webif_titles.json").read_text(encoding="utf-8")
)


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


@pytest.mark.parametrize("capture", CAPTURED, ids=lambda capture: capture.stem)
@pytest.mark.parametrize("key", [*TITLES["pages"], *TITLES["menus"]])
def test_the_titles_file_lists_the_captured_rows(capture, key):
    """Row by row, in one of its two languages: a pair in the file that the
    controller does not show so would let CI vouch for a wrong pairing."""
    rows = TITLES["pages"].get(key) or TITLES["menus"][key]

    assert _titles(capture, key) in (
        [row[0] for row in rows],
        [row[1] for row in rows],
    )


@pytest.mark.parametrize("capture", CAPTURED, ids=lambda capture: capture.stem)
@pytest.mark.parametrize("key", list(TITLES["pages"]))
def test_the_titles_file_names_the_captured_units(capture, key):
    entries = _page(capture, key)["entries"]
    rows = TITLES["pages"][key]

    assert {
        title: pages.unit(shown)
        for (title, shown), (_, _, unit) in zip(entries, rows, strict=True)
        if pages.number(shown) is not None and pages.unit(shown) != unit
    } == {}
