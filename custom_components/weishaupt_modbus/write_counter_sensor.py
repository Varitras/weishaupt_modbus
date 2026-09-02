"""Two diagnostic sensors showing the EEPROM write counters."""

from datetime import date

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .configentry import MyConfigEntry
from .const import CONST, DEVICES
from .coordinator import WeishauptModbusCoordinator
from .migrate_helpers import device_postfix, unique_id_from_parts
from .weishaupt_modbus_api.write_budget import WriteBudget

DAY_ATTRIBUTE = "day"

TOTAL_WRITES = SensorEntityDescription(
    key="eeprom_writes_total",
    translation_key="eeprom_writes_total",
    state_class=SensorStateClass.TOTAL_INCREASING,
    entity_category=EntityCategory.DIAGNOSTIC,
)
WRITES_TODAY = SensorEntityDescription(
    key="eeprom_writes_today",
    translation_key="eeprom_writes_today",
    state_class=SensorStateClass.MEASUREMENT,
    entity_category=EntityCategory.DIAGNOSTIC,
)
WRITE_COUNTER_DESCRIPTIONS = (TOTAL_WRITES, WRITES_TODAY)


class WriteCounterSensor(CoordinatorEntity[WeishauptModbusCoordinator], RestoreSensor):
    """A counter that lives in the client and survives a restart here.

    The client is rebuilt on every (re)load with its counters at zero; the
    last recorded state seeds it again in async_added_to_hass. The state
    catches up with a write on the next poll.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WeishauptModbusCoordinator,
        config_entry: MyConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        """Attach the counter to the entry's system device."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = unique_id_from_parts(config_entry.data, description.key)
        self._attr_device_info = DeviceInfo(
            identifiers={
                (CONST.DOMAIN, DEVICES.SYS + device_postfix(config_entry.data))
            }
        )

    @property
    def _budget(self) -> WriteBudget:
        return self.coordinator.client.write_budget

    @property
    def _daily(self) -> bool:
        return self.entity_description is WRITES_TODAY

    @property
    def native_value(self) -> int:
        """The counter as the client holds it now."""
        return self._budget.writes_today if self._daily else self._budget.total

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """The day the daily count belongs to, so a restore can tell stale from current."""
        if not self._daily:
            return None
        return {DAY_ATTRIBUTE: self._budget.day.isoformat()}

    async def async_added_to_hass(self) -> None:
        """Seed the fresh client with the last recorded count."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_sensor_data()
        if last_data is None or last_data.native_value is None:
            return
        # RestoreSensor types the value as any sensor value; ours is a count.
        count = int(str(last_data.native_value))
        if not self._daily:
            self._budget.restore_total(count)
            return
        last_state = await self.async_get_last_state()
        day_text = last_state.attributes.get(DAY_ATTRIBUTE) if last_state else None
        if day_text:
            self._budget.restore_today(count, date.fromisoformat(day_text))
