"""The diagnostics download: what the pump answered, without its address."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .configentry import MyConfigEntry
from .const import CONF

TO_REDACT = {CONF.HOST}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MyConfigEntry
) -> dict[str, Any]:
    """The entry, the last poll, the bands the pump serves and every register."""
    coordinator = entry.runtime_data.coordinator
    device = entry.runtime_data.device
    return {
        "entry": {
            "version": entry.version,
            "minor_version": entry.minor_version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "failed_polls": coordinator.failed_polls,
        },
        "bands": {
            f"{low}-{high}": served for (low, high), served in device.present.items()
        },
        "registers": [
            {
                "address": item.address,
                "key": item.translation_key,
                "state": item.state,
                "no_sensor": item.is_invalid,
                "off": item.is_off,
            }
            for item in device.items
        ],
        "write_counters": {
            "total": device.write_budget.total,
            "today": device.write_budget.writes_today,
        },
    }
