"""What a loaded entry carries at runtime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util.hass_dict import HassKey

from .const import CONF, CONST

if TYPE_CHECKING:
    from .coordinator import WeishauptModbusCoordinator
    from .webif.client import Client
    from .webif_coordinator import WebifCoordinator


@dataclass
class MyData:
    """The poller (and through it the pump) and the power map of one entry."""

    coordinator: WeishauptModbusCoordinator
    powermap: Any


type MyConfigEntry = ConfigEntry[MyData]


@dataclass
class WebifData:
    """The poller of a web interface entry, and the client it logs out with."""

    coordinator: WebifCoordinator
    client: Client


type WebifConfigEntry = ConfigEntry[WebifData]

# One per pump address, shared by every entry that talks to that controller.
HOST_LOCKS: HassKey[dict[str, asyncio.Lock]] = HassKey(f"{CONST.DOMAIN}_host_locks")


def host_lock(hass: HomeAssistant, host: str) -> asyncio.Lock:
    """The lock each request to the controller at host takes, Modbus or web."""
    return hass.data.setdefault(HOST_LOCKS, {}).setdefault(host, asyncio.Lock())


def is_web_interface(entry: ConfigEntry) -> bool:
    """A pump's web interface rather than the pump over Modbus."""
    return entry.data.get(CONF.KIND) == CONST.WEB_INTERFACE
