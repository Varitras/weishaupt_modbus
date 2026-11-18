"""The settings written through the web interface as a choice: an Evoblock's modulation."""

from collections.abc import Mapping
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICES
from .entities import device_info, name_prefix
from .migrate_helpers import unique_id_from_parts
from .webif import setting
from .webif.discovery import MODULATION_PAGE
from .webif_coordinator import WebifCoordinator
from .webif_sensor import MODULATION

# The options a WEB 13/20 offered, 2026-11-18. The form is read before each
# save: one it does not offer is not sent.
AUTOMATIC_OPTION = "auto"
MODULATION_PERCENTS = range(50, 101, 5)
MODULATION_OPTIONS = {
    AUTOMATIC_OPTION: setting.AUTOMATIC,
    **{str(percent): percent for percent in MODULATION_PERCENTS},
}


class ModulationSelect(CoordinatorEntity[WebifCoordinator], SelectEntity):
    """Wärmepumpe › Modulation: the heat pump's output while the hot water loads.

    User wish, 2026-11-17: set to 50 % at the pump so far. Like the numbers,
    it shows only what the controller reads back.
    """

    _attr_has_entity_name = True
    _attr_translation_key = MODULATION.translation_key
    _attr_options = list(MODULATION_OPTIONS)
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: WebifCoordinator, pump_data: Mapping[str, Any]
    ) -> None:
        """Place the choice on the web interface's device, named like its sensors."""
        super().__init__(coordinator)
        self._attr_unique_id = unique_id_from_parts(
            pump_data, f"webif_{MODULATION.key}"
        )
        self._attr_device_info = device_info(pump_data, DEVICES.WEBIF)
        self._attr_translation_placeholders = {
            "prefix": name_prefix(pump_data, MODULATION.topic)
        }

    @property
    def available(self) -> bool:
        """As long as the entry is loaded, with or without a reading.

        Home Assistant drops a call to an unavailable entity without a word.
        """
        return True

    @property
    def current_option(self) -> str | None:
        """The option the form has selected; unknown once polling stopped."""
        if not self.coordinator.last_update_success:
            return None
        values = (self.coordinator.data or {}).get(MODULATION_PAGE) or {}
        option = setting.MODULATION.option(values.get(MODULATION.title, ""))
        return next(
            (name for name, value in MODULATION_OPTIONS.items() if value == option),
            None,
        )

    async def async_select_option(self, option: str) -> None:
        """Have the controller set it; returns once it reads back, raises otherwise."""
        await self.coordinator.set_setting(
            MODULATION_PAGE, setting.MODULATION, MODULATION_OPTIONS[option]
        )
