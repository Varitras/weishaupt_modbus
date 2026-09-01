"""Setting up sensor entities."""

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
    """Set up the sensor platform."""
    coordinator = config_entry.runtime_data.coordinator
    entries: list[Any] = await build_entity_list(
        entries=[],
        config_entry=config_entry,
        api_items=coordinator.modbus_items,
        item_types=(TYPES.NUMBER_RO, TYPES.SENSOR_CALC, TYPES.SENSOR),
        coordinator=coordinator,
    )
    async_add_entities(entries, update_before_add=True)
