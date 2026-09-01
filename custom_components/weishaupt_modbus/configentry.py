"""my config entry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from .coordinator import WeishauptModbusCoordinator


@dataclass
class MyData:
    """My config data."""

    modbus_api: Any
    config_dir: str
    hass: HomeAssistant
    coordinator: WeishauptModbusCoordinator
    powermap: Any


type MyConfigEntry = ConfigEntry[MyData]
