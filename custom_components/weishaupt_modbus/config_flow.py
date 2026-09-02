"""Config flow."""

from typing import Any

from aiofiles.os import scandir
import voluptuous as vol

from homeassistant import config_entries, exceptions
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv

from .const import CONF, CONST
from .kennfeld import get_filepath


async def build_kennfeld_list(hass: HomeAssistant) -> list[str]:
    """Browse integration directory for heat pump operation map ("kennfeld") files."""
    kennfelder = []
    try:
        dir_iterator = await scandir(get_filepath(hass))
        for item in dir_iterator:
            if "kennfeld.json" in item.name:
                kennfelder.append(item.name)
    except OSError:
        pass

    if len(kennfelder) < 1:
        kennfelder.append("weishaupt_wbb_kennfeld.json")

    return kennfelder


async def validate_input(data: dict[str, Any]) -> dict[str, Any]:
    """Validate the input."""
    if len(data.get(CONF.HOST, "")) < 3:
        raise InvalidHost
    return {"title": data[CONF.HOST]}


class ConfigFlow(config_entries.ConfigFlow, domain=CONST.DOMAIN):  # pylint: disable=abstract-method
    """Class config flow."""

    VERSION = 9
    MINOR_VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

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
                await validate_input(user_input)
                self._stored_data.update(user_input)
                return self.async_create_entry(
                    title=self._stored_data[CONF.HOST], data=self._stored_data
                )

            except InvalidHost:
                errors["base"] = "invalid_host"
            except Exception:
                errors["base"] = "unknown"

        # Define Schema for Page 1
        schema_page1 = vol.Schema(
            schema={
                vol.Required(
                    schema=CONF.HOST,
                    default=self._stored_data.get(CONF.HOST, ""),
                ): str,
                vol.Optional(
                    schema=CONF.PORT,
                    default=self._stored_data.get(CONF.PORT, "502"),
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
                        CONF.KENNFELD_FILE, "weishaupt_wbb_kennfeld.json"
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
                await validate_input(user_input)
                self._stored_data.update(user_input)
                # Not async_update_and_abort: that helper only exists from Home
                # Assistant 2025.8 on, and the declared minimum is 2025.7. The
                # update listener in __init__ reloads the entry.
                self.hass.config_entries.async_update_entry(
                    self._reconfigure_entry, data=self._stored_data
                )
                return self.async_abort(reason="reconfigure_successful")
            except InvalidHost:
                errors["base"] = "invalid_host"
            except Exception:
                errors["base"] = "unknown"

        # We display the same schema as user step 1 for consistency
        schema_reconfigure = vol.Schema(
            schema={
                vol.Required(
                    schema=CONF.HOST,
                    default=self._stored_data.get(CONF.HOST),
                ): str,
                vol.Optional(
                    schema=CONF.PORT,
                    default=self._stored_data.get(CONF.PORT, "502"),
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


class InvalidHost(exceptions.HomeAssistantError):
    """Error to indicate there is an invalid hostname."""


class ConnectionFailed(exceptions.HomeAssistantError):
    """Error to indicate there is an invalid hostname."""
