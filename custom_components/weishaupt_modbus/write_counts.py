"""The write counts of an entry, kept apart from the sensors that show them.

They lived in the sensors' last states: a disabled sensor restored nothing,
so a reload freed the daily limit again, and a sensor added a second time
under its old id added its count a second time.
"""

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import async_get as async_get_restored
from homeassistant.helpers.storage import Store

from .const import CONST
from .migrate_helpers import unique_id_from_parts
from .weishaupt_modbus_api.write_budget import DAY, TODAY, TOTAL, WriteBudget
from .write_counter_sensor import DAY_ATTRIBUTE

STORE_VERSION = 1
# One store per entry for the whole run: a reload reads what a save still
# pending holds, which a new store would read from the file before it lands.
STORES = f"{CONST.DOMAIN}_write_count_stores"
# A burst of writes is saved once; Home Assistant saves what is pending when
# it stops.
SAVE_DELAY_SECONDS = 1


def _store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    stores: dict[str, Store[dict[str, Any]]] = hass.data.setdefault(STORES, {})
    if entry_id not in stores:
        stores[entry_id] = Store(
            hass, STORE_VERSION, f"{CONST.DOMAIN}.{entry_id}.write_counts"
        )
    return stores[entry_id]


def _count(state: State | None) -> int:
    try:
        return int(float(state.state)) if state else 0
    except ValueError:
        return 0  # unknown or unavailable when it was last recorded


def _recorded_by_the_sensors(
    hass: HomeAssistant, id_data: Mapping[str, Any], keys: tuple[str, str]
) -> dict[str, Any]:
    """The counts the sensors held before the store: taken over once."""
    registry = er.async_get(hass)
    last_states = async_get_restored(hass).last_states

    def last_state(key: str) -> State | None:
        unique_id = unique_id_from_parts(id_data, key)
        entity_id = registry.async_get_entity_id("sensor", CONST.DOMAIN, unique_id)
        stored = last_states.get(entity_id) if entity_id else None
        return stored.state if stored else None

    total, today = (last_state(key) for key in keys)
    return {
        TOTAL: _count(total),
        TODAY: _count(today),
        DAY: today.attributes.get(DAY_ATTRIBUTE) if today else None,
    }


async def async_keep_write_counts(
    hass: HomeAssistant,
    entry: ConfigEntry,
    budget: WriteBudget,
    id_data: Mapping[str, Any],
    keys: tuple[str, str],
) -> None:
    """Load the entry's counts into its budget and save each counted write.

    `keys` name the total and the daily counter sensor, whose last states
    seed the store on the first start with it.
    """
    store = _store(hass, entry.entry_id)
    counts = await store.async_load()
    if counts is None:
        counts = _recorded_by_the_sensors(hass, id_data, keys)
        store.async_delay_save(lambda: counts, 0)
    budget.load(counts)
    entry.async_on_unload(
        budget.add_listener(
            lambda: store.async_delay_save(budget.counts, SAVE_DELAY_SECONDS)
        )
    )


async def async_remove_write_counts(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the counts with their entry; an entry set up anew starts at 0."""
    await _store(hass, entry_id).async_remove()
    hass.data[STORES].pop(entry_id, None)
