"""The Update Coordinator for the ModbusItems."""

import asyncio
from datetime import timedelta
import logging
from typing import Any

from modbus_connection import ModbusError

from custom_components.weishaupt_modbus.weishaupt_modbus_api.const import (
    DEFAULT_WRITE_LIMIT_PER_DAY,
    DEFAULT_WRITE_WARNING_PER_DAY,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .configentry import MyConfigEntry
from .const import CONF, CONST, DeviceConstants
from .items import ModbusItem
from .weishaupt_modbus_api.device import WeishauptHeatPump
from .weishaupt_modbus_api.write_budget import WriteBudget

_LOGGER = logging.getLogger(__name__)

# The library gives up on the first block the link cannot serve, so a whole
# refresh takes at most one request timeout longer than a healthy one.
UPDATE_TIMEOUT_SECONDS = 60


def check_configured(modbus_item: ModbusItem, config_entry: MyConfigEntry) -> bool:
    """Whether the entry enables the circuit this item belongs to."""
    match modbus_item.device:
        case DeviceConstants.HZ2:
            return config_entry.data[CONF.HK2]
        case DeviceConstants.HZ3:
            return config_entry.data[CONF.HK3]
        case DeviceConstants.HZ4:
            return config_entry.data[CONF.HK4]
        case DeviceConstants.HZ5:
            return config_entry.data[CONF.HK5]
        case _:
            return True


def scan_interval(config_entry: MyConfigEntry) -> timedelta:
    """The poll interval from the entry's options, or the default."""
    seconds = config_entry.options.get(
        CONST.OPTION_SCAN_INTERVAL, CONST.SCAN_INTERVAL.total_seconds()
    )
    return timedelta(seconds=seconds)


def write_budget(config_entry: MyConfigEntry) -> WriteBudget:
    """The write counters with the thresholds from the entry's options.

    The day rolls over at local midnight, not UTC: that is when a user reads
    "writes today".
    """
    return WriteBudget(
        warn_at=config_entry.options.get(
            CONST.OPTION_WRITE_WARNING_PER_DAY, DEFAULT_WRITE_WARNING_PER_DAY
        ),
        limit=config_entry.options.get(
            CONST.OPTION_WRITE_LIMIT_PER_DAY, DEFAULT_WRITE_LIMIT_PER_DAY
        ),
        today=lambda: dt_util.now().date(),
    )


class WeishauptModbusCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the pump on the entry's interval and hands the rows to the entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        device: WeishauptHeatPump,
        api_items: list[ModbusItem],
        p_config_entry: MyConfigEntry,
    ) -> None:
        """Initialize the coordinator without synchronization overhead."""
        super().__init__(
            hass,
            _LOGGER,
            name="weishaupt-modbus-coordinator",
            update_interval=scan_interval(p_config_entry),
            always_update=True,
        )
        self.device = device
        self._modbusitems = api_items
        self.modbus_items = api_items
        self._config_entry = p_config_entry

    def get_value_from_item(self, translation_key: str) -> Any:
        """Read a value from another modbus item by its translation key."""
        for item in self._modbusitems:
            if item.translation_key == translation_key:
                return item.state
        return None

    async def _async_update_data(self) -> dict[str, Any]:
        """Read every band; a link problem is a failed refresh."""
        try:
            async with asyncio.timeout(UPDATE_TIMEOUT_SECONDS):
                await self.device.async_update()
        except (TimeoutError, ModbusError) as err:
            raise UpdateFailed(f"Modbus communication failure: {err}") from err
        return self._results()

    def _results(self) -> dict[str, Any]:
        """The rows by translation key; a calculated sensor never gets a register value."""
        return {item.translation_key: item.state for item in self._modbusitems}
