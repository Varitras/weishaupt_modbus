"""What a loaded entry carries at runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util.hass_dict import HassKey

from .const import CONF, CONST
from .webif.client import Pacing

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
    """What a web interface entry runs on.

    The poller, the client it logs out with, and the pump entry's data its
    entities are named and placed by.
    """

    coordinator: WebifCoordinator
    client: Client
    pump_data: Mapping[str, Any]


type WebifConfigEntry = ConfigEntry[WebifData]

# One per pump address, shared by every entry that talks to that controller.
HOST_LOCKS: HassKey[dict[str, asyncio.Lock]] = HassKey(f"{CONST.DOMAIN}_host_locks")


def host_lock(hass: HomeAssistant, host: str) -> asyncio.Lock:
    """The lock each request to the controller at host takes, Modbus or web."""
    return hass.data.setdefault(HOST_LOCKS, {}).setdefault(host, asyncio.Lock())


# One per pump address: the entry's client and a dialog's keep the gap together.
HOST_PACINGS: HassKey[dict[str, Pacing]] = HassKey(f"{CONST.DOMAIN}_host_pacings")


def host_pacing(hass: HomeAssistant, host: str) -> Pacing:
    """The gap every web interface client of the controller at host keeps."""
    return hass.data.setdefault(HOST_PACINGS, {}).setdefault(host, Pacing())


async def holding_its_rounds[T](
    entry: ConfigEntry | None, visit: Coroutine[Any, Any, T]
) -> T:
    """A dialog's visit, with the rounds of the running entry it is for held.

    Both keep the gap together, and a dialog that works out reloads the
    entry anyway.
    """
    if entry is None or entry.state is not ConfigEntryState.LOADED:
        return await visit
    async with entry.runtime_data.coordinator.dialog_visiting():
        return await visit


def is_web_interface(entry: ConfigEntry) -> bool:
    """A pump's web interface rather than the pump over Modbus."""
    return entry.data.get(CONF.KIND) == CONST.WEB_INTERFACE


def web_interface_title(pump_title: str) -> str:
    """The title a pump's web interface entry is given, after the pump's."""
    return f"{pump_title} web interface"
