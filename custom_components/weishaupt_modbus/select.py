"""Select."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .configentry import MyConfigEntry
from .const import TYPES
from .entity_helpers import build_entity_list

# Per platform; the device's write lock is what serialises writes across
# all of them, since the controller serves a single client.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Select entry setup."""
    coordinator = config_entry.runtime_data.coordinator

    entries = build_entity_list(
        config_entry=config_entry,
        item_types=TYPES.SELECT,
        coordinator=coordinator,
    )

    # The first refresh already ran and every entity takes its initial value
    # from it; update_before_add would ask for a second full scan.
    async_add_entities(entries)
