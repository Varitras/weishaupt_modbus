"""What a loaded entry carries at runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import WeishauptModbusCoordinator
    from .weishaupt_modbus_api.device import WeishauptHeatPump


@dataclass
class MyData:
    """The pump, its poller and the power map of one config entry."""

    device: WeishauptHeatPump
    coordinator: WeishauptModbusCoordinator
    powermap: Any


type MyConfigEntry = ConfigEntry[MyData]
