"""Number."""

from __future__ import annotations

from typing import cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .configentry import MyConfigEntry, WebifConfigEntry, is_web_interface
from .const import CONF, TYPES
from .entity_helpers import build_entity_list
from .webif.discovery import CIRCUITS, circuit_settings_page
from .webif_number import MinimumTemperatureNumber, PowerLimitNumber
from .webif_sensor import MINIMUM_TEMPERATURES, entry_model

# No limit: with one, Home Assistant queued each call behind the last, and
# the power limit's quiet time never saw two calls to make one write of. The
# Modbus writes are serialised by the device's write lock.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the number platform."""
    if is_web_interface(config_entry):
        web = cast(WebifConfigEntry, config_entry).runtime_data
        polled = config_entry.data[CONF.PAGES]
        model = entry_model(config_entry.data)
        async_add_entities(
            [
                PowerLimitNumber(web.coordinator, web.pump_data),
                *(
                    MinimumTemperatureNumber(web.coordinator, web.pump_data, circuit)
                    for circuit in CIRCUITS
                    if circuit_settings_page(circuit) in polled
                    and model in MINIMUM_TEMPERATURES[circuit].models
                ),
            ]
        )
        return
    coordinator = config_entry.runtime_data.coordinator

    entries = build_entity_list(
        config_entry=config_entry,
        item_types=TYPES.NUMBER,
        coordinator=coordinator,
    )

    # The first refresh already ran and every entity takes its initial value
    # from it; update_before_add would ask for a second full scan.
    async_add_entities(entries)
