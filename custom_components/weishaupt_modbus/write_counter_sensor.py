"""Diagnostic sensors counting writes: the pump's over Modbus, and its web interface's."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
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


class WriteCounterSensor(CoordinatorEntity[DataUpdateCoordinator[Any]], RestoreSensor):
    """A counter that lives in its write budget and survives a restart here.

    The budget is rebuilt on every (re)load with its counters at zero; the
    last recorded state seeds it again in async_added_to_hass. Every counted
    write is written to the state at once: a reload before the next poll
    would otherwise restore a count that is already behind.
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
    def native_value(self) -> int:
        """The counter as the budget holds it now."""
        if self.entity_description.daily:
            return self._budget.writes_today
        return self._budget.total

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """The day the daily count belongs to, so a restore can tell stale from current."""
        if not self.entity_description.daily:
            return None
        return {DAY_ATTRIBUTE: self._budget.day.isoformat()}

    async def async_added_to_hass(self) -> None:
        """Seed the fresh budget with the last recorded count."""
        await super().async_added_to_hass()
        self.async_on_remove(self._budget.add_listener(self.async_write_ha_state))
        last_data = await self.async_get_last_sensor_data()
        if last_data is None or last_data.native_value is None:
            return
        # RestoreSensor types the value as any sensor value; ours is a count.
        count = int(str(last_data.native_value))
        if not self.entity_description.daily:
            self._budget.restore_total(count)
            return
        last_state = await self.async_get_last_state()
        day_text = last_state.attributes.get(DAY_ATTRIBUTE) if last_state else None
        if day_text:
            self._budget.restore_today(count, date.fromisoformat(day_text))


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
