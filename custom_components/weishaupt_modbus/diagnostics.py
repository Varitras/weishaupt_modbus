"""The diagnostics download: what the pump answered, not what identifies the home."""

from __future__ import annotations

from typing import Any, cast

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .configentry import MyConfigEntry, WebifConfigEntry, is_web_interface
from .const import CONF

# Shown as stored; every other key is hidden. Host, prefix, postfix and the
# map's file name are free text the user typed or chose, a family name or a
# room maybe, and the download is meant for a public issue. A key another
# version left in the entry may be a password.
SHOWN = {
    CONF.PORT,
    CONF.HK2,
    CONF.HK3,
    CONF.HK4,
    CONF.HK5,
    CONF.NAME_DEVICE_PREFIX,
    CONF.NAME_TOPIC_PREFIX,
}
# The page addresses are menu codes of the controller firmware, with a field
# the title capture treats as the device's: of no use in an issue either way.
# The pump title is free text again.
WEB_TO_REDACT = {CONF.USERNAME, CONF.PASSWORD, CONF.PAGES, CONF.PUMP_TITLE}


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
            "data": async_redact_data(dict(entry.data), set(entry.data) - SHOWN),
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
            **coordinator.diagnostics(),
        },
    }
