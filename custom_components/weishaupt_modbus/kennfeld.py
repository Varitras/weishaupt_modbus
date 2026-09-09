"""The power map: rated heating power over outside and flow temperature.

Every grid ships compiled (see .github/scripts/compile_kennfeld.py for how
a new one is made); at runtime it is only read, interpolated and drawn.
"""

from collections.abc import Mapping
import json
import logging
from pathlib import Path
import re
import shutil
import sys
from typing import Any
import xml.etree.ElementTree as ET

from homeassistant.core import HomeAssistant

from .configentry import MyConfigEntry
from .const import CONF, CONST
from .migrate_helpers import device_postfix

_LOGGER = logging.getLogger(__name__)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        loaded: dict[str, Any] = json.load(handle)
    return loaded


# Interpolating takes the distance between two accepted values, and
# float(a - b) overflows where a and b are each finite on their own. Half the
# largest float is the largest value whose differences all still fit.
LARGEST_USABLE = sys.float_info.max / 2


def _is_usable_number(value: Any) -> bool:
    """Whether the value is a number this map can compute with.

    One comparison answers for every input, where isfinite() does not: it
    raises for an integer wider than a float rather than saying no. Infinity
    and not-a-number fail the comparison as they should.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return abs(value) <= LARGEST_USABLE


def _numbers(values: Any, at_least: int) -> bool:
    return (
        isinstance(values, list)
        and len(values) >= at_least
        and all(_is_usable_number(v) for v in values)
    )


def _raw_bounds(known_x: list[float]) -> list[int] | None:
    """The outside-temperature bounds as the compiled grid's integer keys.

    None when they do not survive the conversion, so that accepting a grid
    and being able to look up in it are one decision, not two.
    """
    try:
        return [round(min(known_x) * 10), round(max(known_x) * 10)]
    except OverflowError:
        return None


def _looks_like_a_grid(data: Any) -> bool:
    """Whether the file has the shape map() indexes without checking each call.

    The numbers too: an empty known_x, a flow temperature that is not a number
    and a row of strings all passed a shape check and then raised out of
    setup. Flow temperatures must be ascending, because the rows are: sorting
    only the temperatures swapped the curves of a hand-edited file.
    """
    if not isinstance(data, dict):
        return False
    known_t, known_x = data.get("known_t") or [], data.get("known_x") or []
    grid = data.get("compiled_grid")
    if not _numbers(known_t, at_least=2) or known_t != sorted(set(known_t)):
        return False
    if not _numbers(known_x, at_least=1) or _raw_bounds(known_x) is None:
        return False
    if grid is None:
        return True  # reported separately: not compiled
    return isinstance(grid, dict) and all(
        _numbers(row, at_least=len(known_t)) and len(row) == len(known_t)
        for row in grid.values()
    )


# What a drawing is made of, tag names without their namespace. An allowlist,
# because the denylist it replaced grew one entry per payload and still let
# through what it had never been told about.
STATIC_ELEMENTS = frozenset(
    {
        "svg", "g", "defs", "title", "desc", "metadata", "style", "use",
        "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
        "text", "tspan", "clippath", "marker",
        "lineargradient", "radialgradient", "stop",
    }
)  # fmt: skip
# Attributes that name something to fetch; only a fragment of this document.
REFERENCING_ATTRIBUTES = frozenset({"href", "src"})
SAME_DOCUMENT = re.compile(r"#[\w.:-]*\Z")
# Anything that loads or executes from an attribute or a stylesheet.
LOADS_OR_RUNS = re.compile(
    r"javascript:|https?:|data:|url\(|@import|\A//", re.IGNORECASE
)
# CSS may write any identifier or URL as an escape sequence, so `@\69mport`
# is `@import` and the search above reads it as text. Reading CSS properly
# means a CSS parser; refusing the escapes costs nothing here, because no
# generated preview contains a backslash at all.
CSS_ESCAPE = re.compile(r"\\")
# A processing instruction can pull in a stylesheet, and ElementTree drops it
# from the tree, so the filter would never see it. Only the declaration stays.
FOREIGN_INSTRUCTION = re.compile(r"<\?(?!xml[\s?])")


def _canonical(value: str) -> str:
    """Strip what a URL parser strips before it reads the scheme.

    ASCII tab and newline are removed first, so `java&#9;script:` is
    `javascript:` by the time a browser decides what to do with it.
    """
    return value.translate({0x09: None, 0x0A: None, 0x0D: None})


def is_static_picture(svg: str) -> bool:
    """Whether an SVG carries nothing that runs or loads.

    Parsed, not grepped: a substring test let an `onload` handler, a
    namespace-prefixed script, a single-quoted external image and a
    `javascript:` link through. A custom grid may come with a picture from
    anywhere; under www/ it would run in Home Assistant's origin.
    """
    # No document type or entity declarations: nothing for the parser to
    # expand, which is what makes the stdlib parser safe enough here (S314).
    if re.search(r"<!(?:DOCTYPE|ENTITY)", svg, re.IGNORECASE):
        return False
    if FOREIGN_INSTRUCTION.search(svg):
        return False
    try:
        root = ET.fromstring(svg)  # noqa: S314
    except ET.ParseError:
        return False
    return all(_element_is_static(element) for element in root.iter())


def _element_is_static(element: ET.Element) -> bool:
    tag = str(element.tag).rsplit("}", 1)[-1].lower()
    if tag not in STATIC_ELEMENTS:
        return False
    if tag == "style" and not _css_is_static(element.text or ""):
        return False
    return all(
        _attribute_is_static(name.rsplit("}", 1)[-1].lower(), value)
        for name, value in element.attrib.items()
    )


def _attribute_is_static(name: str, value: str) -> bool:
    if name.startswith("on"):
        return False
    if name in REFERENCING_ATTRIBUTES:
        return SAME_DOCUMENT.fullmatch(_canonical(value)) is not None
    # Every other attribute goes through the CSS policy, not only style:
    # SVG presentation attributes (fill, stroke, filter, clip-path, the
    # marker family) are CSS property values, and guarding style alone left
    # the same escaped url() accepted one attribute over.
    return _css_is_static(value)


def _css_is_static(css: str) -> bool:
    """Whether a stylesheet or style attribute neither loads nor escapes."""
    canonical = _canonical(css)
    return not CSS_ESCAPE.search(canonical) and not LOADS_OR_RUNS.search(canonical)


def powermap_file_name(entry_data: Mapping[str, Any]) -> str:
    """The preview under www/local - one per entry, so two pumps do not race for it."""
    return f"{CONST.DOMAIN}_powermap{device_postfix(entry_data)}.svg"


class PowerMap:
    """The compiled power-map grid of one entry, and its preview under www/local.

    Reads only: compiling a grid and drawing its preview happen once, in
    .github/scripts/compile_kennfeld.py, not on every user's machine.
    """

    def __init__(self, config_entry: MyConfigEntry, hass: HomeAssistant) -> None:
        """Initialize the PowerMap."""
        self.hass = hass
        self._config_entry = config_entry
        self._compiled_grid: dict[str, list[float]] = {}
        self._known_t: list[int] = [35, 55]
        self._out_range_raw: list[int] = [-300, 400]

    async def initialize(self) -> None:
        """Load the compiled grid and put its preview under www/local."""
        filepath = Path(
            get_filepath(self.hass) / self._config_entry.data[CONF.KENNFELD_FILE]
        )
        try:
            data = await self.hass.async_add_executor_job(_load_json, filepath)
        except (OSError, ValueError) as err:
            # ValueError covers a corrupt JSON file: the heat power is one
            # sensor, not a reason to fail the whole entry's setup.
            _LOGGER.error("Failed to load power map file %s: %s", filepath, err)
            return
        if not _looks_like_a_grid(data):
            _LOGGER.error(
                "Power map %s is not a compiled grid (known_t with at least two "
                "flow temperatures, one value per curve and outside temperature). "
                "The heat power stays unknown",
                filepath.name,
            )
            return
        if "compiled_grid" not in data:
            _LOGGER.error(
                "Power map %s has no compiled grid; compile it with "
                ".github/scripts/compile_kennfeld.py. The heat power stays unknown",
                filepath.name,
            )
            return

        self._known_t = list(data["known_t"])
        known_x = data["known_x"]
        # Rounded to whole tenths: the compiled keys are integers, and a
        # known_x of -10.0 clamped to "-100.0", which no grid has. The shape
        # check above already refused a known_x this cannot convert.
        raw_bounds = _raw_bounds(known_x)
        assert raw_bounds is not None
        self._out_range_raw = raw_bounds
        self._compiled_grid = data["compiled_grid"]

        www_dir = Path(f"{self.hass.config.config_dir}/www/local")
        await self.hass.async_add_executor_job(
            self._copy_powermap_plot, filepath, www_dir
        )

    def _copy_powermap_plot(self, json_filepath: Path, www_dir: Path) -> None:
        """Copy the compiled SVG from the kennfeld folder to Home Assistant's local www directory.

        Runs inside the executor thread pool.
        """
        png_src = json_filepath.with_suffix(".svg")
        if not png_src.exists():
            _LOGGER.info(
                "No preview %s beside the power map; draw one with "
                ".github/scripts/compile_kennfeld.py",
                png_src.name,
            )
            return
        try:
            picture = png_src.read_text(encoding="utf-8")
        except (OSError, ValueError) as err:
            # As for the grid file: the preview is an optional decoration, and
            # a file that is not UTF-8 must not take the entry down with it.
            _LOGGER.error(
                "Preview %s cannot be read and is not copied: %s", png_src.name, err
            )
            return
        try:
            if not is_static_picture(picture):
                _LOGGER.error(
                    "Preview %s carries script or links to the web and is not "
                    "copied under www/, where it would run in Home Assistant's "
                    "origin. Redraw it with compile_kennfeld.py",
                    png_src.name,
                )
                return
            # Ensure the /config/www/local directory exists
            www_dir.mkdir(parents=True, exist_ok=True)

            # Destination file path
            png_dest = www_dir / powermap_file_name(self._config_entry.data)

            # Perform metadata-preserving copy
            shutil.copy2(png_src, png_dest)
            _LOGGER.info(
                "Successfully updated dashboard power map image: %s", png_dest.name
            )
        except OSError as err:
            _LOGGER.error(
                "Failed to copy power map image to local www directory: %s", err
            )

    def map(self, outside_temp_raw: float, flow_temp_raw: float) -> float | None:
        """Rated power at an outside and flow temperature (raw tenths), interpolated.

        None without a map: 0 W here became a recorded heat output of zero for
        a configuration problem.
        """
        if not self._compiled_grid:
            return None

        # Convert flow temp to actual °C for fractional interpolation
        flow_temp = flow_temp_raw / 10

        # Clamp raw outside temperature and actual flow temperature
        outside_raw = max(
            self._out_range_raw[0],
            min(round(outside_temp_raw), self._out_range_raw[1]),
        )
        flow_temp = max(
            float(self._known_t[0]), min(flow_temp, float(self._known_t[-1]))
        )

        # 1. Direct O(1) outside temperature key lookup
        vals = self._compiled_grid.get(str(outside_raw))
        if not vals:
            return None

        # 2. Find surrounding Flow Temp curve intervals in known_t
        y0_idx = 0
        for i in range(len(self._known_t) - 1):
            if self._known_t[i] <= flow_temp <= self._known_t[i + 1]:
                y0_idx = i
                break
        else:
            if flow_temp < self._known_t[0]:
                y0_idx = 0
            else:
                y0_idx = len(self._known_t) - 2
        y1_idx = y0_idx + 1

        # 3. Grab the 2 flow temp boundary values at this outside temperature
        p0 = vals[y0_idx]  # flow y0
        p1 = vals[y1_idx]  # flow y1

        # Calculate fractional flow temp delta
        dy = (flow_temp - self._known_t[y0_idx]) / (
            self._known_t[y1_idx] - self._known_t[y0_idx]
        )

        # 1D Linear Interpolation between the curves
        return p0 + dy * (p1 - p0)


def get_filepath(hass: HomeAssistant) -> Path:
    """Get the filepath to the custom component directory."""
    filepath = Path(
        f"{hass.config.config_dir}/custom_components/{CONST.DOMAIN}/kennfeld"
    )
    if not filepath.exists():
        filepath = Path(__file__).resolve().parent / "kennfeld"
    return filepath
