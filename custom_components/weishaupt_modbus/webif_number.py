"""The heating power limit as a number: the one setting written through the web interface."""

from collections.abc import Mapping
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONST, DEVICES
from .entities import device_info, name_prefix
from .migrate_helpers import unique_id_from_parts
from .webif.discovery import HEATING_PAGE
from .webif.setting import shown_percent
from .webif_coordinator import WebifCoordinator
from .webif_sensor import POWER_LIMIT

# The controller's own list: 10 to 100 % in steps of one.
POWER_LIMIT_MIN = 10
POWER_LIMIT_MAX = 100


class PowerLimitNumber(CoordinatorEntity[WebifCoordinator], NumberEntity):
    """Wärmepumpe › Heizen › Leistungsbegrenzung, as the heating page shows it.

    It shows only what the controller reads back: a value set here appears
    once the write's own read of the heating page confirms it.
    """

    _attr_has_entity_name = True
    _attr_translation_key = POWER_LIMIT.translation_key
    _attr_native_min_value = POWER_LIMIT_MIN
    _attr_native_max_value = POWER_LIMIT_MAX
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: WebifCoordinator, pump_data: Mapping[str, Any]
    ) -> None:
        """Place the number on the web interface's device, named like its sensors."""
        super().__init__(coordinator)
        self._attr_unique_id = unique_id_from_parts(
            pump_data, f"webif_{POWER_LIMIT.key}"
        )
        self._attr_device_info = device_info(pump_data, DEVICES.WEBIF)
        self._attr_translation_placeholders = {
            "prefix": name_prefix(pump_data, POWER_LIMIT.topic)
        }

    @property
    def available(self) -> bool:
        """Only while the heating page's last values are still shown."""
        return super().available and self._values() is not None

    @property
    def native_value(self) -> int | None:
        """The power limit the heating page shows."""
        return shown_percent((self._values() or {}).get(POWER_LIMIT.title, ""))

    async def async_set_native_value(self, value: float) -> None:
        """Have the controller set it; returns once it reads back, raises otherwise."""
        if not value.is_integer():
            raise ServiceValidationError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_power_limit_whole_percent",
                translation_placeholders={"value": str(value)},
            )
        await self.coordinator.set_power_limit(int(value))

    def _values(self) -> dict[str, str] | None:
        # No data at all until the first round, which runs after the setup.
        return (self.coordinator.data or {}).get(HEATING_PAGE)
