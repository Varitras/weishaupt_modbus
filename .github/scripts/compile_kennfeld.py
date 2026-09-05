"""Compile a raw power-map grid into the form the integration ships.

A raw grid names a few points per flow-temperature curve (known_x, known_y,
known_t); the integration reads a compiled_grid with a value per 0.1 °C of
outside temperature. Compiling needs numpy (scipy makes the curve smoother),
which is why it happens here, once, and not on every user's machine.

    python .github/scripts/compile_kennfeld.py custom_components/weishaupt_modbus/kennfeld/my_kennfeld.json

It also draws the preview picture next to the grid (needs pygal); the
integration only copies that picture under www/ and refuses one that
carries script.
"""

import json
from pathlib import Path
import re
import sys


def sorted_curves(known_t: list, known_y: list) -> tuple[list, list]:
    """The flow temperatures ascending, each with the curve that belongs to it.

    Sorting known_t alone reordered the temperatures away from their curves:
    an unsorted raw grid then shipped the 35 degC curve under 55 degC.
    """
    pairs = sorted(zip(known_t, known_y, strict=True), key=lambda pair: pair[0])
    return [t for t, _ in pairs], [ys for _, ys in pairs]


def compile_grid(data: dict) -> dict[str, list[float]]:
    """A value per 0.1 °C of outside temperature for every flow curve."""
    known_x = data["known_x"]
    known_t, known_y = sorted_curves(data["known_t"], data["known_y"])
    try:
        from scipy.interpolate import CubicSpline  # noqa: PLC0415

        curves = [CubicSpline(known_x, ys, bc_type="natural") for ys in known_y]
    except ImportError:
        from numpy.polynomial import Chebyshev  # noqa: PLC0415

        print("scipy not installed - using a Chebyshev fit", file=sys.stderr)
        curves = [Chebyshev.fit(known_x, ys, deg=8) for ys in known_y]
    assert len(curves) == len(known_t), "one curve per flow temperature"
    return {
        str(raw): [round(float(curve(raw / 10.0)), 1) for curve in curves]
        for raw in range(min(known_x) * 10, max(known_x) * 10 + 1)
    }


def draw_preview(data: dict, svg_path: Path) -> bool:
    """A static SVG of the flow curves; False when pygal is not installed."""
    try:
        import pygal  # noqa: PLC0415
        from pygal.style import Style  # noqa: PLC0415
    except ImportError:
        print("pygal not installed - no preview drawn", file=sys.stderr)
        return False
    grid = data["compiled_grid"]
    known_t = data["known_t"]
    # Home Assistant's dark cards
    style = Style(
        background="#1c1c1e",
        plot_background="#1c1c1e",
        foreground="#e5e5ea",
        foreground_strong="#ffffff",
        foreground_subtle="#8e8e93",
        colors=("#30d158", "#0a84ff", "#ff453a", "#bf5af2"),
        stroke_width=2.5,
    )
    chart = pygal.XY(
        stroke=True,
        show_dots=False,
        width=500,
        height=320,
        style=style,
        legend_at_bottom=True,
        js=[],
    )
    chart.title = f"Kennfeld Heizleistung - {svg_path.stem}"
    for index, flow in enumerate(known_t):
        # one point per whole degree is plenty for a picture
        points = [
            (raw / 10.0, values[index])
            for raw, values in ((int(r), v) for r, v in grid.items())
            if raw % 10 == 0
        ]
        chart.add(f"{flow}°C Vorlauf", sorted(points))
    # pygal inlines its own config as a <script> even with js=[]; a picture
    # under www/ carries no script at all.
    static = re.sub(
        r"<script.*?</script>", "", chart.render().decode("utf-8"), flags=re.DOTALL
    )
    svg_path.write_text(static, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    """Write compiled_grid into the file named on the command line."""
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(argv[1])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["known_t"], data["known_y"] = sorted_curves(data["known_t"], data["known_y"])
    data["compiled_grid"] = compile_grid(data)
    path.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"compiled {path.name}: {len(data['compiled_grid'])} outside temperatures")
    if draw_preview(data, path.with_suffix(".svg")):
        print(f"drew {path.with_suffix('.svg').name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
