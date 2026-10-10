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


@pytest.mark.parametrize("capture", CAPTURED, ids=lambda capture: capture.stem)
@pytest.mark.parametrize("key", list(PATHS))
def test_the_catalogue_reads_the_captured_page(capture, key):
    """Every value read is on the page once, in the unit its sensor expects."""
    captured = json.loads(capture.read_text(encoding="utf-8"))["pages"]
    entries = next(page["entries"] for page in captured if page["page"] == key)
    page = next(
        page
        for page in polled_pages(PATHS, {}, REQUIRED_TITLES, ENGLISH_TITLES)
        if page.key == key
    )
    text = _as_served(key, entries)

    assert page.is_whole(text), f"lacks {sorted(page.missing(text))}"
    assert not shown_in_unknown_units(key, page.read(text))
