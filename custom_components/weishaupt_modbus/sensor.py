"""Setting up sensor entities."""

from typing import Any, cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .configentry import MyConfigEntry, WebifConfigEntry, is_web_interface
from .const import TYPES
from .entity_helpers import build_entity_list
from .webif_sensor import WEBIF_SENSORS, WebifSensor
from .write_counter_sensor import WRITE_COUNTER_DESCRIPTIONS, WriteCounterSensor

# Read only; the coordinator polls for every entity at once.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    if is_web_interface(config_entry):
        web = cast(WebifConfigEntry, config_entry).runtime_data
        async_add_entities(
            WebifSensor(web.coordinator, web.pump_data, description)
            for description in WEBIF_SENSORS
        )
        return
    coordinator = config_entry.runtime_data.coordinator
    entries: list[Any] = build_entity_list(
        config_entry=config_entry,
        item_types=(TYPES.NUMBER_RO, TYPES.SENSOR_CALC, TYPES.SENSOR),
        coordinator=coordinator,
    )
    entries.extend(
        WriteCounterSensor(coordinator, config_entry, description)
        for description in WRITE_COUNTER_DESCRIPTIONS
    )
    # The first refresh already ran and every entity takes its initial value
    # from it; update_before_add would ask for a second full scan.
    async_add_entities(entries)
