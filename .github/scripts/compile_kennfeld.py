"""Compile a raw power-map grid into the form the integration ships.

A raw grid names a few points per flow-temperature curve (known_x, known_y,
known_t); the integration reads a compiled_grid with a value per 0.1 °C of
outside temperature. Compiling needs numpy (scipy makes the curve smoother),
which is why it happens here, once, and not on every user's machine.

    python .github/scripts/compile_kennfeld.py custom_components/weishaupt_modbus/kennfeld/my_kennfeld.json

The integration draws the missing preview SVG at first start.
"""

import json
from pathlib import Path
import sys


def compile_grid(data: dict) -> dict[str, list[float]]:
    """A value per 0.1 °C of outside temperature for every flow curve."""
    known_x = data["known_x"]
    known_y = data["known_y"]
    known_t = sorted(data["known_t"])
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


def main(argv: list[str]) -> int:
    """Write compiled_grid into the file named on the command line."""
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(argv[1])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["known_t"] = sorted(data["known_t"])
    data["compiled_grid"] = compile_grid(data)
    path.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"compiled {path.name}: {len(data['compiled_grid'])} outside temperatures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
