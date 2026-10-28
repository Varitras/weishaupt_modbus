"""The write counts of an entry, kept apart from the sensors that show them.

They lived in the sensors' last states: a disabled sensor restored nothing,
so a reload freed the daily limit again, and a sensor added a second time
under its old id added its count a second time.
"""

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import CONST
from .weishaupt_modbus_api.write_budget import WriteBudget

STORE_VERSION = 1
# One store and the latest counts per entry for the whole run: a reload
# takes the counts from here, as Home Assistant's load reads the file
# without waiting for a save that is writing it.
STORES = f"{CONST.DOMAIN}_write_count_stores"
# A burst of writes is saved once; Home Assistant saves what is pending when
# it stops.
SAVE_DELAY_SECONDS = 1


@dataclass
class _Kept:
    store: Store[dict[str, Any]]
    counts: dict[str, Any] | None = None


def _kept(hass: HomeAssistant, entry_id: str) -> _Kept:
    stores: dict[str, _Kept] = hass.data.setdefault(STORES, {})
    if entry_id not in stores:
        stores[entry_id] = _Kept(
            Store(hass, STORE_VERSION, f"{CONST.DOMAIN}.{entry_id}.write_counts")
        )
    return stores[entry_id]


async def async_keep_write_counts(
    hass: HomeAssistant, entry: ConfigEntry, budget: WriteBudget
) -> None:
    """Load the entry's counts into its budget and save each counted write.

    An entry without a store starts at 0. The counts its sensors held
    before the store are not taken over: they were read from Home
    Assistant's internal last states, which 2026.11 renamed.
    """
    kept = _kept(hass, entry.entry_id)
    counts = kept.counts
    if counts is None:
        counts = await kept.store.async_load() or {}
    kept.counts = counts
    budget.load(counts)

    def keep() -> None:
        latest = kept.counts = budget.counts()
        kept.store.async_delay_save(lambda: latest, SAVE_DELAY_SECONDS)

    entry.async_on_unload(budget.add_listener(keep))


async def async_remove_write_counts(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the counts with their entry; an entry set up anew starts at 0."""
    await _kept(hass, entry_id).store.async_remove()
    hass.data[STORES].pop(entry_id, None)
