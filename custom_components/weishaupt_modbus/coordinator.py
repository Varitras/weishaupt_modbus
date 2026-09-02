"""The Update Coordinator for the ModbusItems."""

import asyncio
from datetime import timedelta
import logging
from typing import Any

from pymodbus import ModbusException

from custom_components.weishaupt_modbus.weishaupt_modbus_api.const import (
    DEFAULT_WRITE_LIMIT_PER_DAY,
    DEFAULT_WRITE_WARNING_PER_DAY,
)
from custom_components.weishaupt_modbus.weishaupt_modbus_api.exceptions import (
    ConnectionFailedError,
)
from custom_components.weishaupt_modbus.weishaupt_modbus_api.modbus_api import (
    WeishauptModbusClient,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .configentry import MyConfigEntry
from .const import CONF, CONST, TYPES, DeviceConstants
from .items import ModbusItem
from .weishaupt_modbus_api.write_budget import WriteBudget

_LOGGER = logging.getLogger(__name__)


async def check_configured(
    modbus_item: ModbusItem, config_entry: MyConfigEntry
) -> bool:
    """Check if item is configured."""
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
    """Clean, lock-free DataUpdateCoordinator for batch Modbus register polling."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: WeishauptModbusClient,
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
        self.client = client
        self._modbusitems = api_items
        self.modbus_items = api_items
        self._config_entry = p_config_entry

    def get_value_from_item(self, translation_key: str) -> Any:
        """Read a value from another modbus item by its translation key."""
        for item in self._modbusitems:
            if item.translation_key == translation_key:
                return item.state
        return None

    async def _async_setup(self) -> None:
        """Verify client connection during integration startup."""
        if not self.client.connected:
            _LOGGER.debug("Establishing initial connection to heat pump...")
            connected = await self.client.connect()
            if not connected:
                raise ConfigEntryNotReady(
                    "Could not establish initial Modbus connection"
                )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all configured registers using high-efficiency batch reads."""
        try:
            # The locking context is shifted down. The coordinator simply triggers the update.
            async with asyncio.timeout(15):
                await self.client.update()

            return await self._process_cached_data()

        except (TimeoutError, ConnectionFailedError, ModbusException) as err:
            raise UpdateFailed(f"Modbus communication failure: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected coordinator update error: {err}") from err

    async def _process_cached_data(self) -> dict[str, Any]:
        """Map the raw/sanitized cache to the expected translation keys."""
        results: dict[str, Any] = {}

        for item in self._modbusitems:
            # Skip items belonging to unconfigured heating circuits
            if not await check_configured(item, self._config_entry):
                continue

            # 1. Catch purely virtual calculated sensors immediately.
            # They never poll Modbus directly and evaluate entirely in-memory.
            if getattr(item, "type", None) == TYPES.SENSOR_CALC:
                item.state = None
                results[item.translation_key] = None
                continue

            address = getattr(item, "_address", None) or getattr(item, "address", None)

            # 2. Process standard Modbus-polled items
            if address is not None:
                # Instantly retrieve the pre-processed register value from client cache
                val = self.client.get_value(address)
                item.state = val
                results[item.translation_key] = val
            else:
                results[item.translation_key] = None

        return results
