"""The sensor catalogue against recordings of a real controller.

Every other test reads pages built from the catalogue itself, so a title or
a unit that differs from the controller passes them all and fails only live.
This one reads what a controller really sent: opt in by pointing
WEISHAUPT_WEBIF_RECORDINGS at the probe tool's capture folder. Without it the
test is skipped. Recordings never enter the repository - they carry a serial
number, an access code and addresses - and only the three polled pages are
opened, chosen by the names the probe recorded them under.
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
from custom_components.weishaupt_modbus.webif_sensor import (
    REQUIRED_TITLES,
    WEBIF_SENSORS,
    reading,
)

RECORDINGS = os.environ.get("WEISHAUPT_WEBIF_RECORDINGS")
pytestmark = pytest.mark.skipif(
    not RECORDINGS, reason="set WEISHAUPT_WEBIF_RECORDINGS to the capture folder"
)

# The probe names a data page after its title in a --pages run, and after its
# menu path in an --explore run ("Info > Statistik" -> "info___statistik").
RECORDED_AS = {
    HEAT_PUMP_PAGE: {"waermepumpe", "info___waermepumpe"},
    STATISTICS_PAGE: {"statistik", "info___statistik"},
    HEATING_PAGE: {"waermepumpe___heizen"},
}
RETRY_SUFFIX = "_retry"
HEADER_END = b"\r\n\r\n"
LAST_CHUNK = 0


def _body(raw: bytes) -> str:
    """The body of a recorded response, chunking undone."""
    head, _, body = raw.partition(HEADER_END)
    if b"transfer-encoding: chunked" not in head.lower():
        return body.decode("utf-8", errors="replace")
    whole = b""
    while body:
        size_line, _, rest = body.partition(b"\r\n")
        size = int(size_line.split(b";")[0], 16)
        if size == LAST_CHUNK:
            break
        whole += rest[:size]
        body = rest[size + len(b"\r\n") :]
    return whole.decode("utf-8", errors="replace")


def _recorded(key: str) -> list[list[tuple[str, str]]]:
    """Titles and values of every recording of the page, whole or not."""
    found = []
    for index in sorted(pathlib.Path(RECORDINGS).glob("*/capture.json")):
        for record in json.loads(index.read_text(encoding="utf-8"))["requests"]:
            name = record["name"].removesuffix(RETRY_SUFFIX)
            if record["status"] != 200 or name not in RECORDED_AS[key]:
                continue
            raw = index.parent / f"{record['number']:02d}_{record['name']}.response.raw"
            text = _body(raw.read_bytes())
            if key == HEATING_PAGE:
                children = pages.children(text, record["path"])
                found.append([(child.title, child.text) for child in children])
            else:
                found.append(pages.values(text))
    return found


@pytest.mark.parametrize("key", list(RECORDED_AS))
def test_the_catalogue_reads_the_recorded_page(key):
    """Every sensor's title is on a whole recording, in the unit it expects."""
    required = REQUIRED_TITLES[key]
    recordings = _recorded(key)
    whole = [found for found in recordings if pages.is_complete(found, required)]
    closest = min(
        (required - {title for title, _ in found} for found in recordings),
        key=len,
        default=required,
    )
    assert whole, f"no whole recording of {key}; the closest lacks {sorted(closest)}"

    sensors = {sensor.title: sensor for sensor in WEBIF_SENSORS if sensor.page == key}
    wrong = {
        title: shown
        for found in whole
        for title, shown in found
        if title in sensors
        and shown not in (pages.NO_VALUE, pages.OFF)
        and reading(sensors[title], shown) is None
    }
    assert not wrong, f"shown in another unit than the catalogue expects: {wrong}"
