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
from typing import Any

from homeassistant.core import HomeAssistant

from .configentry import MyConfigEntry
from .const import CONF, CONST
from .migrate_helpers import device_postfix

_LOGGER = logging.getLogger(__name__)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        loaded: dict[str, Any] = json.load(handle)
    return loaded


def _looks_like_a_grid(data: Any) -> bool:
    """Whether the file has the shape map() indexes without checking each call."""
    if not isinstance(data, dict):
        return False
    known_t = data.get("known_t")
    grid = data.get("compiled_grid")
    if not isinstance(known_t, list) or len(known_t) < 2:
        return False
    if grid is None:
        return True  # reported separately: not compiled
    return isinstance(grid, dict) and all(
        isinstance(row, list) and len(row) == len(known_t) for row in grid.values()
    )


def powermap_file_name(entry_data: Mapping[str, Any]) -> str:
    """The preview under www/local - one per entry, so two pumps do not race for it."""
    return f"{CONST.DOMAIN}_powermap{device_postfix(entry_data)}.svg"


class PowerMap:
    """PowerMap class that loads pre-compiled grids and renders dynamic Pygal SVG graphs."""

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

        self._known_t = sorted(data.get("known_t", [35, 55]))
        known_x = data.get("known_x", [-30, 40])
        self._out_range_raw = [min(known_x) * 10, max(known_x) * 10]
        self._compiled_grid = data["compiled_grid"]

        if not filepath.with_suffix(".svg").exists():
            await self.hass.async_add_executor_job(
                self._generate_plot_blocking, data, filepath
            )
        www_dir = Path(f"{self.hass.config.config_dir}/www/local")
        await self.hass.async_add_executor_job(
            self._copy_powermap_plot, filepath, www_dir
        )

    def _generate_plot_blocking(self, data: dict[str, Any], filepath: Path) -> None:
        """Generate an SVG plot using Pygal."""
        import pygal  # noqa: PLC0415
        from pygal.style import Style  # noqa: PLC0415

        compiled_grid: dict[str, Any] = data.get("compiled_grid") or {}
        known_t = sorted(data.get("known_t", [35, 55]))
        known_x = data.get("known_x", [-30, 40])

        # Determine the ranges
        out_min_raw = min(known_x) * 10
        out_max_raw = max(known_x) * 10
        raw_range = range(
            out_min_raw, out_max_raw + 10, 10
        )  # 1°C steps for rendering speed

        # Custom dark-theme style to match Home Assistant Cards
        custom_style = Style(
            background="#1c1c1e",
            plot_background="#1c1c1e",
            foreground="#e5e5ea",
            foreground_strong="#ffffff",
            foreground_subtle="#8e8e93",
            colors=("#30d158", "#0a84ff", "#ff453a", "#bf5af2"),
            stroke_width=2.5,
        )

        try:
            chart = pygal.XY(
                stroke=True,
                show_dots=False,
                width=500,
                height=320,
                style=custom_style,
                legend_at_bottom=True,
                # A static picture: no inline script, no JavaScript fetched
                # from the web into Home Assistant's origin.
                js=[],
            )
            chart.title = f"Kennfeld Heizleistung - {filepath.stem}"

            for r_idx, flow_val in enumerate(known_t):
                # Retrieve the values for this flow temperature curve
                curve_points = []
                for r in raw_range:
                    if str(r) in compiled_grid:
                        curve_points.append((r / 10.0, compiled_grid[str(r)][r_idx]))

                if curve_points:
                    chart.add(f"{flow_val}°C Vorlauf", curve_points)

            svg_path = filepath.with_suffix(".svg")
            # pygal inlines its own config as a <script> even with js=[];
            # a picture under www/ carries no script at all.
            static = re.sub(
                r"<script.*?</script>",
                "",
                chart.render().decode("utf-8"),
                flags=re.DOTALL,
            )
            svg_path.write_text(static, encoding="utf-8")
            _LOGGER.info("Successfully generated missing SVG plot: %s", svg_path.name)
        except Exception as err:
            _LOGGER.debug("Pygal SVG plot generation failed: %s", err)

    def _copy_powermap_plot(self, json_filepath: Path, www_dir: Path) -> None:
        """Copy the compiled SVG from the kennfeld folder to Home Assistant's local www directory.

        Runs inside the executor thread pool.
        """
        png_src = json_filepath.with_suffix(".svg")
        if not png_src.exists():
            _LOGGER.debug("No power map plot found at %s to copy", png_src.name)
            return

        try:
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
