"""Select."""

from __future__ import annotations

from typing import cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .configentry import MyConfigEntry, WebifConfigEntry, is_web_interface
from .const import TYPES
from .entity_helpers import build_entity_list
from .webif.discovery import MODULATION_PAGE
from .webif_select import ModulationSelect
from .webif_sensor import MODULATION, entry_model

# No limit: with one, Home Assistant queued each call behind the last, and
# the modulation's quiet time never saw two choices to make one write of. The
# Modbus writes are serialised by the device's write lock.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Select entry setup."""
    if is_web_interface(config_entry):
        web = cast(WebifConfigEntry, config_entry).runtime_data
        if web.coordinator.polls(MODULATION_PAGE) and (
            entry_model(config_entry.data) in MODULATION.models
        ):
            async_add_entities([ModulationSelect(web.coordinator, web.pump_data)])
        return
    coordinator = config_entry.runtime_data.coordinator

    entries = build_entity_list(
        config_entry=config_entry,
        item_types=TYPES.SELECT,
        coordinator=coordinator,
    )

    # The first refresh already ran and every entity takes its initial value
    # from it; update_before_add would ask for a second full scan.
    async_add_entities(entries)
