"""Switch: on/off for setpoints with an off word."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .configentry import MyConfigEntry
from .const import TYPES
from .entity_helpers import build_entity_list


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switch platform."""
    # start with an empty list of entries
    entries: list[Any] = []

    # we create one communicator per integration only for better performance and to allow dynamic parameters
    coordinator = config_entry.runtime_data.coordinator

    entries = await build_entity_list(
        entries=entries,
        config_entry=config_entry,
        api_items=coordinator.modbus_items,
        item_types=TYPES.NUMBER,
        coordinator=coordinator,
        as_off_switch=True,
    )

    # The first refresh already ran and every entity takes its initial value
    # from it; update_before_add would ask for a second full scan.
    async_add_entities(entries)
