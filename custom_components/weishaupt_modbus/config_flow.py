"""Config flow."""

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant import config_entries, exceptions
from homeassistant.core import HomeAssistant, callback
import homeassistant.helpers.config_validation as cv

from .const import CONF, CONST
from .kennfeld import get_filepath
from .migrate_helpers import entry_unique_id
from .weishaupt_modbus_api.const import (
    DEFAULT_PORT,
    DEFAULT_WRITE_LIMIT_PER_DAY,
    DEFAULT_WRITE_WARNING_PER_DAY,
    EEPROM_WRITE_RATING,
)


def _kennfeld_files(folder: Path) -> list[str]:
    try:
        found = sorted(p.name for p in folder.iterdir() if "kennfeld.json" in p.name)
    except OSError:
        found = []
    return found or [CONST.DEF_KENNFELDFILE]


async def build_kennfeld_list(hass: HomeAssistant) -> list[str]:
    """The power-map files a user can pick from."""
    return await hass.async_add_executor_job(_kennfeld_files, get_filepath(hass))


def validate_input(data: dict[str, Any]) -> None:
    """Normalise the host in place; raise InvalidHost for one that cannot be dialled."""
    host = str(data.get(CONF.HOST, "")).strip()
    unusable = len(host) < 3 or any(character.isspace() for character in host)
    if unusable:
        raise InvalidHost
    data[CONF.HOST] = host


class ConfigFlow(config_entries.ConfigFlow, domain=CONST.DOMAIN):  # pylint: disable=abstract-method
    """Class config flow."""

    VERSION = 11
    MINOR_VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow for this entry."""
        return OptionsFlow()

    def __init__(self) -> None:
        """Initialize the flow."""
        self._stored_data: dict[str, Any] = {}
        self._reconfigure_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1: Core configuration setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                validate_input(user_input)
            except InvalidHost:
                errors["base"] = "invalid_host"
        if user_input is not None and not errors:
            await self.async_set_unique_id(entry_unique_id(user_input))
            self._abort_if_unique_id_configured()
            self._stored_data.update(user_input)
            return self.async_create_entry(
                title=self._stored_data[CONF.HOST], data=self._stored_data
            )

        # Define Schema for Page 1
        schema_page1 = vol.Schema(
            schema={
                vol.Required(
                    schema=CONF.HOST,
                    default=self._stored_data.get(CONF.HOST, ""),
                ): str,
                vol.Optional(
                    schema=CONF.PORT,
                    default=self._stored_data.get(CONF.PORT, DEFAULT_PORT),
                ): cv.port,
                vol.Optional(
                    schema=CONF.PREFIX,
                    default=self._stored_data.get(CONF.PREFIX, CONST.DEF_PREFIX),
                ): str,
                vol.Optional(
                    schema=CONF.DEVICE_POSTFIX,
                    default=self._stored_data.get(CONF.DEVICE_POSTFIX, ""),
                ): str,
                vol.Optional(
                    schema=CONF.KENNFELD_FILE,
                    default=self._stored_data.get(
                        CONF.KENNFELD_FILE, CONST.DEF_KENNFELDFILE
                    ),
                ): vol.In(container=await build_kennfeld_list(self.hass)),
                vol.Optional(
                    schema=CONF.HK2,
                    default=self._stored_data.get(CONF.HK2, False),
                ): bool,
                vol.Optional(
                    schema=CONF.HK3,
                    default=self._stored_data.get(CONF.HK3, False),
                ): bool,
                vol.Optional(
                    schema=CONF.HK4,
                    default=self._stored_data.get(CONF.HK4, False),
                ): bool,
                vol.Optional(
                    schema=CONF.HK5,
                    default=self._stored_data.get(CONF.HK5, False),
                ): bool,
                vol.Optional(
                    schema=CONF.NAME_DEVICE_PREFIX,
                    default=self._stored_data.get(CONF.NAME_DEVICE_PREFIX, False),
                ): bool,
                vol.Optional(
                    schema=CONF.NAME_TOPIC_PREFIX,
                    default=self._stored_data.get(CONF.NAME_TOPIC_PREFIX, False),
                ): bool,
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=schema_page1, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Trigger a reconfiguration flow."""
        errors: dict[str, str] = {}
        self._reconfigure_entry = self._get_reconfigure_entry()

        # Pre-seed internal state dictionary with the current saved entry data
        if not self._stored_data:
            self._stored_data.update(self._reconfigure_entry.data)

        if user_input is not None:
            try:
                validate_input(user_input)
            except InvalidHost:
                errors["base"] = "invalid_host"
        if user_input is not None and not errors:
            self._stored_data.update(user_input)
            # Not async_update_reload_and_abort: that schedules a reload of
            # its own while the update listener in __init__ reloads too.
            self.hass.config_entries.async_update_entry(
                self._reconfigure_entry,
                data=self._stored_data,
                unique_id=entry_unique_id(self._stored_data),
            )
            return self.async_abort(reason="reconfigure_successful")

        # We display the same schema as user step 1 for consistency
        schema_reconfigure = vol.Schema(
            schema={
                vol.Required(
                    schema=CONF.HOST,
                    default=self._stored_data.get(CONF.HOST),
                ): str,
                vol.Optional(
                    schema=CONF.PORT,
                    default=self._stored_data.get(CONF.PORT, DEFAULT_PORT),
                ): cv.port,
                vol.Optional(
                    schema=CONF.PREFIX,
                    default=self._stored_data.get(CONF.PREFIX, CONST.DEF_PREFIX),
                ): str,
                vol.Optional(
                    schema=CONF.DEVICE_POSTFIX,
                    default=self._stored_data.get(CONF.DEVICE_POSTFIX, ""),
                ): str,
                vol.Optional(
                    schema=CONF.KENNFELD_FILE,
                    default=self._stored_data.get(CONF.KENNFELD_FILE),
                ): vol.In(container=await build_kennfeld_list(hass=self.hass)),
                vol.Optional(
                    schema=CONF.HK2,
                    default=self._stored_data.get(CONF.HK2, False),
                ): bool,
                vol.Optional(
                    schema=CONF.HK3,
                    default=self._stored_data.get(CONF.HK3, False),
                ): bool,
                vol.Optional(
                    schema=CONF.HK4,
                    default=self._stored_data.get(CONF.HK4, False),
                ): bool,
                vol.Optional(
                    schema=CONF.HK5,
                    default=self._stored_data.get(CONF.HK5, False),
                ): bool,
                vol.Optional(
                    schema=CONF.NAME_DEVICE_PREFIX,
                    default=self._stored_data.get(CONF.NAME_DEVICE_PREFIX, False),
                ): bool,
                vol.Optional(
                    schema=CONF.NAME_TOPIC_PREFIX,
                    default=self._stored_data.get(CONF.NAME_TOPIC_PREFIX, False),
                ): bool,
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema_reconfigure,
            errors=errors,
            description_placeholders={
                CONF.HOST: "myhostname",
            },
        )


class OptionsFlow(config_entries.OptionsFlow):
    """Runtime settings that do not need a new entry.

    Stored in entry.options; the update listener in __init__ reloads the
    entry when they change.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """The one options page."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        current_interval = options.get(
            CONST.OPTION_SCAN_INTERVAL, int(CONST.SCAN_INTERVAL.total_seconds())
        )
        current_warning = options.get(
            CONST.OPTION_WRITE_WARNING_PER_DAY, DEFAULT_WRITE_WARNING_PER_DAY
        )
        current_limit = options.get(
            CONST.OPTION_WRITE_LIMIT_PER_DAY, DEFAULT_WRITE_LIMIT_PER_DAY
        )
        # 0 switches the warning or the limit off; anything up to the EEPROM's
        # lifetime rating is a choice the user may make.
        writes_per_day = vol.All(
            vol.Coerce(int), vol.Range(min=0, max=EEPROM_WRITE_RATING)
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONST.OPTION_SCAN_INTERVAL, default=current_interval
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=CONST.SCAN_INTERVAL_MIN_SECONDS,
                        max=CONST.SCAN_INTERVAL_MAX_SECONDS,
                    ),
                ),
                vol.Required(
                    CONST.OPTION_WRITE_WARNING_PER_DAY, default=current_warning
                ): writes_per_day,
                vol.Required(
                    CONST.OPTION_WRITE_LIMIT_PER_DAY, default=current_limit
                ): writes_per_day,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


class InvalidHost(exceptions.HomeAssistantError):
    """Error to indicate there is an invalid hostname."""
