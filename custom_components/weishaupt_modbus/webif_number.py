"""The settings written through the web interface, as numbers.

The heating power limit, and on an Evoblock each circuit's minimum flow
temperature.
"""

from collections.abc import Mapping
from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONST, DEVICES
from .entities import device_info, name_prefix
from .migrate_helpers import unique_id_from_parts
from .webif.discovery import HEATING_PAGE, circuit_settings_page
from .webif.setting import MINIMUM_TEMPERATURE, shown_percent, shown_tenths
from .webif_coordinator import WebifCoordinator
from .webif_sensor import MINIMUM_TEMPERATURES, POWER_LIMIT

# The list a WBB offers, 10 to 100 % in steps of one. Another controller's
# form is read before each save: a target it does not offer is not sent.
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
        """As long as the entry is loaded, with or without a reading.

        Home Assistant drops a call to an unavailable entity without a word:
        the write would neither run nor say why it was refused.
        """
        return True

    @property
    def native_value(self) -> int | None:
        """The power limit the heating page shows; unknown once polling stopped.

        After a stop or a refused login the last reading stays in the
        coordinator, while the controller may long since hold another value.
        """
        if not self.coordinator.last_update_success:
            return None
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


# The list a WEB 13/20 offered, 10.0 °C up to the circuit's maximum in steps
# of 0.5 (2026-11-17). The form is read before each save, as above.
MINIMUM_TEMPERATURE_MIN = 10.0
MINIMUM_TEMPERATURE_STEP = 0.5
# The highest maximum the controller offered: until a reading shows the
# circuit's own.
MINIMUM_TEMPERATURE_MAX = 70.0
MAXIMUM_TEMPERATURE_TITLE = "Maximaltemperatur"


class MinimumTemperatureNumber(CoordinatorEntity[WebifCoordinator], NumberEntity):
    """Heizkreis n › Einstellungen › Minimaltemperatur, as its page shows it.

    User wish, 2026-11-17: raised by hand at the pump on windy days so far.
    Like the power limit, it shows only what the controller reads back.
    """

    _attr_has_entity_name = True
    _attr_native_min_value = MINIMUM_TEMPERATURE_MIN
    _attr_native_step = MINIMUM_TEMPERATURE_STEP
    _attr_mode = NumberMode.BOX
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: WebifCoordinator,
        pump_data: Mapping[str, Any],
        circuit: int,
    ) -> None:
        """Place the number on the web interface's device, named by its circuit."""
        super().__init__(coordinator)
        description = MINIMUM_TEMPERATURES[circuit]
        self._page = circuit_settings_page(circuit)
        self._attr_translation_key = description.translation_key
        self._attr_unique_id = unique_id_from_parts(
            pump_data, f"webif_{description.key}"
        )
        self._attr_device_info = device_info(pump_data, DEVICES.WEBIF)
        self._attr_translation_placeholders = {
            "prefix": name_prefix(pump_data, description.topic)
        }

    @property
    def available(self) -> bool:
        """As long as the entry is loaded, with or without a reading."""
        return True

    @property
    def native_value(self) -> float | None:
        """The minimum the settings page shows; unknown once polling stopped."""
        if not self.coordinator.last_update_success:
            return None
        tenths = shown_tenths((self._values() or {}).get(MINIMUM_TEMPERATURE.title, ""))
        return None if tenths is None else tenths / 10

    @property
    def native_max_value(self) -> float:
        """The circuit's maximum temperature, which the controller offers last."""
        shown = (self._values() or {}).get(MAXIMUM_TEMPERATURE_TITLE, "")
        tenths = shown_tenths(shown)
        return MINIMUM_TEMPERATURE_MAX if tenths is None else tenths / 10

    async def async_set_native_value(self, value: float) -> None:
        """Have the controller set it; returns once it reads back, raises otherwise."""
        tenths = round(value * 10)
        if abs(value * 10 - tenths) > 1e-6 or tenths % 5:
            raise ServiceValidationError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_minimum_temperature_step",
                translation_placeholders={"value": str(value)},
            )
        await self.coordinator.set_setting(self._page, MINIMUM_TEMPERATURE, tenths)

    def _values(self) -> dict[str, str] | None:
        # No data at all until the first round, which runs after the setup.
        return (self.coordinator.data or {}).get(self._page)
