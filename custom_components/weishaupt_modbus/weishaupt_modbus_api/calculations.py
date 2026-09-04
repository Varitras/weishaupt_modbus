"""The calculated sensors' formulas.

Plain functions: the first argument is the sensor's own register (already
divided), then the sibling registers named in its params, raw. A formula
that needs the power map takes it last.
"""

from typing import Protocol


class PowerMap(Protocol):
    """What a formula needs from the power map: the rated power at a point."""

    def map(self, outside_temp_raw: float, flow_temp_raw: float) -> float | None:
        """Rated heating power in W at an outside and a flow temperature (raw tenths); None without a map."""


def heat_output(
    power_request_percent: float,
    air_intake_temperature_raw: float,
    flow_temperature_raw: float,
    power_map: PowerMap,
) -> float | None:
    """Heating power in W: the request in percent of the map's rated power."""
    rated = power_map.map(air_intake_temperature_raw, flow_temperature_raw)
    if rated is None:
        return None
    return power_request_percent / 100 * rated


def spread(flow_temperature: float, return_temperature_raw: float) -> float:
    """Flow minus return, in °C."""
    return flow_temperature - return_temperature_raw / 10


def performance_factor(heat_energy: float, electric_energy: float) -> float | None:
    """Heat energy per electric energy, both in kWh; undefined without electric energy.

    At the start of a day, month or year the electric counter is 0 and the
    factor is undefined - a 0.0 in its place was a plausible, false number.
    """
    if electric_energy == 0:
        return None
    return heat_energy / electric_energy
