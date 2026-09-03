"""Entity classes used in this integration."""

import logging
from typing import Any

from modbus_connection import ModbusError

from homeassistant.components.number import NumberEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .configentry import MyConfigEntry
from .const import CONF, CONST, FORMATS
from .coordinator import WeishauptModbusCoordinator
from .items import ModbusItem
from .migrate_helpers import create_unique_id, device_postfix
from .weishaupt_modbus_api.exceptions import WriteError
from .weishaupt_modbus_api.hpconst import reverse_device_list

_LOGGER = logging.getLogger(__name__)


def to_register_value(value: float, divider: int) -> int:
    """The register word for a user value: 1.15 at divider 100 is 115, not 114.

    int() truncates, and 1.15 * 100 is 114.99999999999999 in binary floating
    point - every allowed heating-curve value with two decimals is affected.
    """
    return round(float(value) * divider)


class MyEntity(CoordinatorEntity[WeishauptModbusCoordinator]):
    """What every entity of a register row shares: naming, unit, limits, writes."""

    _divider: int = 1
    _attr_has_entity_name = True
    _dynamic_min: float | None = None
    _dynamic_max: float | None = None
    _has_dynamic_min = False
    _has_dynamic_max = False

    def __init__(
        self,
        coordinator: WeishauptModbusCoordinator,
        config_entry: MyConfigEntry,
        api_item: ModbusItem,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._api_item: ModbusItem = api_item

        dev_postfix = device_postfix(self._config_entry.data)
        dev_prefix = self._config_entry.data[CONF.PREFIX]

        if self._config_entry.data[CONF.NAME_DEVICE_PREFIX]:
            name_device_prefix = dev_prefix + "_"
        else:
            name_device_prefix = ""

        if self._config_entry.data[CONF.NAME_TOPIC_PREFIX]:
            device_key = self._api_item.device
            name_topic_prefix = f"{reverse_device_list.get(device_key, 'UK')}_"
        else:
            name_topic_prefix = ""

        name_prefix = name_topic_prefix + name_device_prefix

        self._attr_device_info = DeviceInfo(
            identifiers={(CONST.DOMAIN, self._api_item.device + dev_postfix)},
            translation_key=self._api_item.device,
            translation_placeholders={"postfix": dev_postfix},
            manufacturer="Weishaupt",
        )

        self._attr_translation_key = self._api_item.translation_key
        self._attr_translation_placeholders = {"prefix": name_prefix}

        self._attr_unique_id = create_unique_id(self._config_entry, self._api_item)

        if self._api_item.format == FORMATS.STATUS:
            self._divider = 1
        elif self._api_item.format == FORMATS.TEXT:
            self._attr_suggested_display_precision = None
        else:
            if self._api_item.params is not None:
                self._attr_native_unit_of_measurement = self._api_item.params.get(
                    "unit", ""
                )
                self._attr_native_step = self._api_item.params.get("step", 1)
                self._divider = self._api_item.params.get("divider", 1)
                self._attr_device_class = self._api_item.params.get("deviceclass", None)
                self._attr_suggested_display_precision = self._api_item.params.get(
                    "precision", 2
                )
                self._attr_native_min_value = self._api_item.params.get("min", -999999)
                self._attr_native_max_value = self._api_item.params.get("max", 999999)
                if self._api_item.params.get("dynamic_min", None) is not None:
                    self._has_dynamic_min = True
                if self._api_item.params.get("dynamic_max", None) is not None:
                    self._has_dynamic_max = True
            self.set_min_max()

        if self._api_item.params is not None:
            icon = self._api_item.params.get("icon", None)
            if icon is not None:
                self._attr_icon = icon

    def set_min_max(self, onlydynamic: bool = False) -> None:
        """Set min max to fixed or dynamic values."""
        if onlydynamic is True:
            if (self._has_dynamic_min is False) & (self._has_dynamic_max is False):
                return

        if self._has_dynamic_min:
            min_key = self._api_item.params.get("dynamic_min") or ""
            # Safely fetch the dynamic min from the coordinator
            self._dynamic_min = self.coordinator.get_value_from_item(min_key)
            if self._dynamic_min is not None:
                self._attr_native_min_value = self._dynamic_min / self._divider

        if self._has_dynamic_max:
            max_key = self._api_item.params.get("dynamic_max") or ""
            # Safely fetch the dynamic max from the coordinator
            self._dynamic_max = self.coordinator.get_value_from_item(max_key)
            if self._dynamic_max is not None:
                self._attr_native_max_value = self._dynamic_max / self._divider

    def translate_val(self, val: Any) -> float | str | None:
        """Translate modbus value into senseful format."""
        if self._api_item.format == FORMATS.STATUS:
            return self._api_item.get_translation_key_from_number(val)

        if val is None:
            return None
        self.set_min_max(True)
        return float(val) / self._divider

    async def set_translate_val(self, value: str | float) -> int | None:
        """Translate and write a value directly to the Modbus client."""
        if self._api_item.format == FORMATS.STATUS:
            val = self._api_item.get_number_from_translation_key(str(value))
        else:
            self.set_min_max(True)
            val = to_register_value(float(value), self._divider)

        if val is None:
            return None

        # Raised, not logged: a refused or failed write has to reach the user
        # who moved the slider and the automation that called the service.
        try:
            await self.coordinator.device.write(self._api_item, val)
        except (WriteError, ModbusError) as err:
            raise HomeAssistantError(
                f"Writing register {self._api_item.address} failed: {err}"
            ) from err
        return val


class MySensorEntity(MyEntity, SensorEntity):
    """A read-only register as a sensor."""

    def __init__(
        self,
        config_entry: MyConfigEntry,
        modbus_item: ModbusItem,
        coordinator: WeishauptModbusCoordinator,
        idx: int,
    ) -> None:
        """Initialize of MySensorEntity."""
        super().__init__(coordinator, config_entry, modbus_item)

        # Set sensor-specific state class
        if modbus_item.format in [
            FORMATS.TEMPERATURE,
            FORMATS.PERCENTAGE,
            FORMATS.NUMBER,
            FORMATS.UNKNOWN,
        ]:
            # default state class to record all entities by default
            self._attr_state_class = SensorStateClass.MEASUREMENT
            if modbus_item.params is not None:
                self._attr_state_class = modbus_item.params.get(
                    "stateclass", SensorStateClass.MEASUREMENT
                )
        if modbus_item.format == FORMATS.TEXT:
            # self._attr_state_class = SensorStateClass.NONE
            self._attr_suggested_display_precision = None
        # The first refresh ran before the platforms; the listener only fires
        # on the next one, so without this every sensor reads unknown for a
        # whole scan interval after setup.
        self._attr_native_value = self.translate_val(modbus_item.state)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = self.translate_val(self._api_item.state)
        self.async_write_ha_state()


class MyCalcSensorEntity(MySensorEntity):
    """A sensor computed from other registers by a function in calculations.py."""

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = self.translate_val(self._api_item.state)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """When entity is added to Hass, perform immediate initial calculation."""
        await super().async_added_to_hass()
        # Force a calculation using the standard sensors' freshly loaded boot states
        self._attr_native_value = self.translate_val(self._api_item.state)
        self.async_write_ha_state()

    def translate_val(self, val: Any) -> float | None:
        """The formula over the own register and its operands; None when any is absent."""
        params = self._api_item.params
        formula = params.get("calculation")
        if formula is None:
            return None
        own = self.coordinator.device.value_of(self._api_item.address)
        siblings = [
            self.coordinator.get_value_from_item(key)
            for key in params.get("operands", ())
        ]
        # No operand, no value: 0.0 in its place made a missing supply
        # temperature read as a spread of -30 °C.
        if own is None or any(sibling is None for sibling in siblings):
            return None
        arguments: list[Any] = [own / self._divider, *siblings]
        if params.get("uses_power_map"):
            arguments.append(self._config_entry.runtime_data.powermap)
        try:
            result = formula(*arguments)
        except ZeroDivisionError:
            return 0.0
        return float(round(result, self._attr_suggested_display_precision))


class MyNumberEntity(MyEntity, NumberEntity):
    """A writable register as a number."""

    def __init__(
        self,
        config_entry: MyConfigEntry,
        modbus_item: ModbusItem,
        coordinator: WeishauptModbusCoordinator,
        idx: int,
    ) -> None:
        """Initialize MyNumberEntity."""
        super().__init__(coordinator, config_entry, modbus_item)
        self._attr_native_value = self.translate_val_number(modbus_item.state)

    def translate_val_number(self, val: Any) -> float | None:
        """Translate modbus value for number entity."""
        if val is None:
            return None
        self.set_min_max(True)
        return float(val) / self._divider

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = self.translate_val_number(self._api_item.state)
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        """Send value over modbus and refresh HA."""
        result = await self.set_translate_val(value)
        if result is not None:
            self._api_item.state = result
            self._attr_native_value = self.translate_val_number(self._api_item.state)
            self.async_write_ha_state()


class MySelectEntity(MyEntity, SelectEntity):
    """A writable status register as a select."""

    def __init__(
        self,
        config_entry: MyConfigEntry,
        modbus_item: ModbusItem,
        coordinator: WeishauptModbusCoordinator,
        idx: int,
    ) -> None:
        """Initialize MySelectEntity."""
        super().__init__(coordinator, config_entry, modbus_item)
        # option list build from the status list of the ModbusItem
        self._attr_options: list[str] = []
        for item in self._api_item.resultlist or []:
            self._attr_options.append(item.translation_key)
        self._attr_current_option = self.translate_val_select(modbus_item.state)

    def translate_val_select(self, val: Any) -> str | None:
        """Translate modbus value for select entity."""
        if self._api_item.format == FORMATS.STATUS:
            result = self._api_item.get_translation_key_from_number(val)
            return str(result) if result is not None else None
        return None

    async def async_select_option(self, option: str) -> None:
        """Write the selected option to modbus and refresh HA."""
        result = await self.set_translate_val(option)
        if result is not None:
            self._api_item.state = result
            self._attr_current_option = self.translate_val_select(self._api_item.state)
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_current_option = self.translate_val_select(self._api_item.state)
        self.async_write_ha_state()
