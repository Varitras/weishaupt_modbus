"""The calculated sensors' formulas.

Plain functions: the first argument is the sensor's own register (already
divided), then the sibling registers named in its params, raw. A formula
that needs the power map takes it last.
"""

from typing import Protocol


class PowerMap(Protocol):
    """What a formula needs from the power map: the rated power at a point."""

    def map(self, air_intake_raw: float, flow_temp_raw: float) -> float | None:
        """Rated heating power in W at an air intake and a flow temperature (raw tenths); None without a map."""

    def map_min(self, air_intake_raw: float, flow_temp_raw: float) -> float | None:
        """Minimum heating power in W at the same point; None for a map without minimum curves."""


def heat_output(
    power_request_percent: float,
    air_intake_temperature_raw: float,
    flow_temperature_raw: float,
    power_map: PowerMap,
) -> float | None:
    """Heating power in W from the request and the power map.

    A map with minimum curves: minimum + request x (maximum - minimum), and
    0 W at 0 % - the compressor is off then, not at its minimum (on a WEB
    13/20 the request reads 0 exactly when the delivered output does, and
    14 % or more while it runs). Checked against flow rate x spread on one
    WEB 13/20 only; on a WBB the same formula read far too high, so a map
    has minimum curves only where they were checked.

    Every other map: the request in percent of the rated power, unchanged.
    """
    rated = power_map.map(air_intake_temperature_raw, flow_temperature_raw)
    if rated is None:
        return None
    minimum = power_map.map_min(air_intake_temperature_raw, flow_temperature_raw)
    if minimum is None:
        return power_request_percent / 100 * rated
    if power_request_percent <= 0:
        return 0.0
    return minimum + power_request_percent / 100 * (rated - minimum)


def spread(flow_temperature: float, return_temperature_raw: float) -> float:
    """Flow minus return, in °C."""
    return flow_temperature - return_temperature_raw / 10


# User decision, 2026-10-10: the energy registers count whole kWh, so below
# this the factor can be off by more than a fifth - 7 kWh of heat for 1 kWh
# of electricity read 7.00 for anything between about 3.5 and 8.
MIN_ELECTRIC_ENERGY_KWH = 5


def performance_factor(heat_energy: float, electric_energy: float) -> float | None:
    """Heat energy per electric energy, both in kWh; undefined below 5 kWh.

    At the start of a day, month or year the electric counter is 0 and the
    factor is undefined - a 0.0 in its place was a plausible, false number.
    """
    if electric_energy < MIN_ELECTRIC_ENERGY_KWH:
        return None
    return heat_energy / electric_energy
