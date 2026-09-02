"""Setting up sensor entities."""

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .configentry import MyConfigEntry
from .const import TYPES
from .entity_helpers import build_entity_list
from .write_counter_sensor import WRITE_COUNTER_DESCRIPTIONS, WriteCounterSensor


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
    entries.extend(
        WriteCounterSensor(coordinator, config_entry, description)
        for description in WRITE_COUNTER_DESCRIPTIONS
    )
    # The first refresh already ran and every entity takes its initial value
    # from it; update_before_add would ask for a second full scan.
    # The first refresh already ran and every entity takes its initial value
    # from it; update_before_add would ask for a second full scan.
    async_add_entities(entries)
