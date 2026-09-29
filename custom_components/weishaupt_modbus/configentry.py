"""What a loaded entry carries at runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import WeishauptModbusCoordinator


@dataclass
class MyData:
    """The poller (and through it the pump) and the power map of one entry."""

    coordinator: WeishauptModbusCoordinator
    powermap: Any


type MyConfigEntry = ConfigEntry[MyData]
