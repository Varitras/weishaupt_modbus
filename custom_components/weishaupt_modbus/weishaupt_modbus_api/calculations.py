"""The calculated sensors' formulas.

Plain functions: the first argument is the sensor's own register (already
divided), then the sibling registers named in its params, raw. A formula
that needs the power map takes it last.
"""

from typing import Protocol


class PowerMap(Protocol):
    """What a formula needs from the power map: the rated power at a point."""

    def map(self, outside_temp_raw: float, flow_temp_raw: float) -> float:
        """Rated heating power in W at an outside and a flow temperature (raw tenths)."""


def heat_output(
    power_request_percent: float,
    air_intake_temperature_raw: float,
    flow_temperature_raw: float,
    power_map: PowerMap,
) -> float:
    """Heating power in W: the request in percent of the map's rated power."""
    return (
        power_request_percent
        / 100
        * power_map.map(air_intake_temperature_raw, flow_temperature_raw)
    )


def spread(flow_temperature: float, return_temperature_raw: float) -> float:
    """Flow minus return, in °C."""
    return flow_temperature - return_temperature_raw / 10


def performance_factor(heat_energy: float, electric_energy: float) -> float:
    """Heat energy per electric energy, both in kWh."""
    return heat_energy / electric_energy
