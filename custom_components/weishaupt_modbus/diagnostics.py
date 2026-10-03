"""The diagnostics download: what the pump answered, not what identifies the home."""

from __future__ import annotations

from typing import Any, cast

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .configentry import MyConfigEntry, WebifConfigEntry, is_web_interface
from .const import CONF

# Free text the user typed or chose: a prefix or postfix is often a family
# name or a room, and the download is meant for a public issue.
TO_REDACT = {CONF.HOST, CONF.PREFIX, CONF.DEVICE_POSTFIX, CONF.KENNFELD_FILE}
# The page addresses carry the controller's own codes, a device token among them.
WEB_TO_REDACT = {CONF.USERNAME, CONF.PASSWORD, CONF.PAGES}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MyConfigEntry
) -> dict[str, Any]:
    """The entry, the last poll, the bands the pump serves and every register."""
    if is_web_interface(entry):
        return _web_interface(cast(WebifConfigEntry, entry))
    coordinator = entry.runtime_data.coordinator
    device = entry.runtime_data.coordinator.device
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
                "invalid": item.is_invalid,
                "off": item.is_off,
            }
            for item in device.items
        ],
        "write_counters": {
            "total": device.write_budget.total,
            "today": device.write_budget.writes_today,
        },
    }


def _web_interface(entry: WebifConfigEntry) -> dict[str, Any]:
    """The entry without its login and page addresses, and each page's last values."""
    coordinator = entry.runtime_data.coordinator
    return {
        "entry": {
            "version": entry.version,
            "minor_version": entry.minor_version,
            "data": async_redact_data(dict(entry.data), WEB_TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "pages": coordinator.data,
            **coordinator.diagnostics(),
        },
    }
