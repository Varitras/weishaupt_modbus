"""Diagnostic sensors counting writes: the pump's over Modbus, and its web interface's."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .configentry import MyConfigEntry
from .const import CONST, DEVICES
from .coordinator import WeishauptModbusCoordinator
from .entities import device_info
from .migrate_helpers import device_postfix, unique_id_from_parts
from .webif_coordinator import WebifCoordinator
from .weishaupt_modbus_api.write_budget import WriteBudget

DAY_ATTRIBUTE = "day"


@dataclass(frozen=True, kw_only=True)
class WriteCounterDescription(SensorEntityDescription):
    """A write counter: the lifetime total, or today's count when `daily`."""

    daily: bool = False


def _counter(key: str, *, daily: bool) -> WriteCounterDescription:
    return WriteCounterDescription(
        key=key,
        translation_key=key,
        state_class=(
            SensorStateClass.MEASUREMENT if daily else SensorStateClass.TOTAL_INCREASING
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
        daily=daily,
    )


TOTAL_WRITES = _counter("eeprom_writes_total", daily=False)
WRITES_TODAY = _counter("eeprom_writes_today", daily=True)
WRITE_COUNTER_DESCRIPTIONS = (TOTAL_WRITES, WRITES_TODAY)
# The settings the web interface wrote, counted apart from the Modbus writes
# and limited by an option of its own.
WEBIF_WRITE_COUNTER_DESCRIPTIONS = (
    _counter("webif_settings_written_total", daily=False),
    _counter("webif_settings_written_today", daily=True),
)


class WriteCounterSensor(CoordinatorEntity[DataUpdateCoordinator[Any]], SensorEntity):
    """A counter that shows its write budget; the entry's store keeps the counts.

    Every counted write is shown at once, not with the next poll.
    """

    _attr_has_entity_name = True
    entity_description: WriteCounterDescription

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[Any],
        budget: WriteBudget,
        unique_id: str,
        device: DeviceInfo,
        description: WriteCounterDescription,
    ) -> None:
        """Attach the counter to its budget and its device."""
        super().__init__(coordinator)
        self.entity_description = description
        self._budget = budget
        self._attr_unique_id = unique_id
        self._attr_device_info = device

    @property
    def available(self) -> bool:
        """Also while the polling fails: the count is this side's own."""
        return True

    @property
    def native_value(self) -> int:
        """The counter as the budget holds it now."""
        if self.entity_description.daily:
            return self._budget.writes_today
        return self._budget.total

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """The day the daily count belongs to."""
        if not self.entity_description.daily:
            return None
        return {DAY_ATTRIBUTE: self._budget.day.isoformat()}

    async def async_added_to_hass(self) -> None:
        """Show each counted write as it is counted."""
        await super().async_added_to_hass()
        self.async_on_remove(self._budget.add_listener(self.async_write_ha_state))

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Renamed in place: the listener writes under the current id."""


def pump_write_counters(
    coordinator: WeishauptModbusCoordinator, config_entry: MyConfigEntry
) -> list[WriteCounterSensor]:
    """The EEPROM write counters, on the pump's system device."""
    device = DeviceInfo(
        identifiers={(CONST.DOMAIN, DEVICES.SYS + device_postfix(config_entry.data))}
    )
    return [
        WriteCounterSensor(
            coordinator,
            coordinator.device.write_budget,
            unique_id_from_parts(config_entry.data, description.key),
            device,
            description,
        )
        for description in WRITE_COUNTER_DESCRIPTIONS
    ]


def web_write_counters(
    coordinator: WebifCoordinator, pump_data: Mapping[str, Any]
) -> list[WriteCounterSensor]:
    """The counters of the settings written, on the web interface's device."""
    return [
        WriteCounterSensor(
            coordinator,
            coordinator.budget,
            unique_id_from_parts(pump_data, description.key),
            device_info(pump_data, DEVICES.WEBIF),
            description,
        )
        for description in WEBIF_WRITE_COUNTER_DESCRIPTIONS
    ]
