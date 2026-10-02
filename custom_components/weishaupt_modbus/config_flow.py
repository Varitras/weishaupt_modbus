"""Config flow."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import aiohttp
from modbus_connection import ModbusError, ModbusTcpParams
import voluptuous as vol

from homeassistant import config_entries, exceptions
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .configentry import host_lock, is_web_interface
from .const import CONF, CONST
from .kennfeld import get_filepath
from .migrate_helpers import entry_unique_id
from .webif.client import Client, LoginRefused, Unreachable, WebifError
from .webif.discovery import find_pages
from .weishaupt_modbus_api.const import (
    DEFAULT_PORT,
    DEFAULT_WRITE_LIMIT_PER_DAY,
    DEFAULT_WRITE_WARNING_PER_DAY,
    EEPROM_WRITE_RATING,
    MODBUS_UNIT_ID,
)


def _kennfeld_files(folder: Path) -> list[str]:
    try:
        found = sorted(
            p.name
            for p in folder.iterdir()
            if p.name.endswith("kennfeld.json") and p.is_file()
        )
    except OSError:
        found = []
    return found or [CONST.DEF_KENNFELDFILE]


async def build_kennfeld_list(hass: HomeAssistant) -> list[str]:
    """The power-map files a user can pick from."""
    return await hass.async_add_executor_job(_kennfeld_files, get_filepath(hass))


# The outside temperature: every model serves it, so one read of it says
# whether there is a Weishaupt controller at the address.
PROBE_REGISTER = 30001
RESERVED_POSTFIXES = f"{CONST.DOMAIN}_reserved_postfixes"


async def pump_answers(hass: HomeAssistant, data: dict[str, Any]) -> bool:
    """Whether a controller answers at the host and port the user entered."""
    params = ModbusTcpParams(
        host=data[CONF.HOST], port=int(data.get(CONF.PORT, DEFAULT_PORT))
    )
    try:
        async with async_get_temporary_unit(hass, params, MODBUS_UNIT_ID) as unit:
            await unit.read_input_registers(PROBE_REGISTER, 1)
    except ModbusError, OSError, TimeoutError:
        return False
    return True


def namespace_error(
    hass: HomeAssistant,
    data: dict[str, Any],
    entry_id: str | None = None,
    pending: set[str] | None = None,
) -> str | None:
    """Why a second pump could not get entities of its own, or None.

    Entity and device ids are built from prefix and postfix only, so two
    entries with the same postfix collide entity for entity - the second
    pump loads with nothing. Once another pump exists the postfix has to
    be set, and set to something no other pump uses.

    ``pending`` are the postfixes other flows are probing with right now:
    two dialogs submitted together both passed this check before either
    entry existed, and one pump then loaded with no entities.
    """
    others = [
        entry
        for entry in hass.config_entries.async_entries(CONST.DOMAIN)
        if entry.entry_id != entry_id and not is_web_interface(entry)
    ]
    taken = {str(entry.data.get(CONF.DEVICE_POSTFIX, "")).strip() for entry in others}
    taken |= pending or set()
    if not taken:
        return None
    postfix = str(data.get(CONF.DEVICE_POSTFIX, "")).strip()
    if not postfix:
        return "postfix_required"
    if postfix in taken:
        return "postfix_in_use"
    return None


async def read_web_interface(
    hass: HomeAssistant, host: str, user: str, password: str
) -> dict[str, str]:
    """The pages to poll, found on a short visit that logs out again.

    Raises WebifError.
    """
    session = async_create_clientsession(
        hass, cookie_jar=aiohttp.CookieJar(unsafe=True)
    )
    client = Client(session, host, user, password, host_lock=host_lock(hass, host))
    try:
        return await find_pages(client)
    finally:
        await client.close()
        await session.close()


def web_interface_error(error: WebifError) -> str:
    """The form's error for a visit that failed."""
    if isinstance(error, LoginRefused):
        return "invalid_auth"
    if isinstance(error, Unreachable):
        return "cannot_connect"
    return "cannot_read"


PASSWORD_FIELD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


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
        if is_web_interface(config_entry):
            return WebifOptionsFlow()
        return OptionsFlow()

    def __init__(self) -> None:
        """Initialize the flow."""
        self._stored_data: dict[str, Any] = {}
        self._reconfigure_entry: config_entries.ConfigEntry | None = None

    def _postfixes_of_other_flows(self) -> set[str]:
        """The postfixes every other open flow of this integration has reserved."""
        return {
            postfix
            for flow_id, postfix in self._reservations().items()
            if flow_id != self.flow_id
        }

    def _reservations(self) -> dict[str, str]:
        """Postfix each open flow is probing with, by flow id, kept on hass.

        Two dialogs submitted together both passed the namespace check before
        either entry existed. The flow's typed context has no room for it.
        """
        reservations: dict[str, str] = self.hass.data.setdefault(RESERVED_POSTFIXES, {})
        return reservations

    @callback
    def async_remove(self) -> None:
        """Release the reservation with the flow, however it ended."""
        self._reservations().pop(self.flow_id, None)

    async def _objection(self, user_input: dict[str, Any]) -> dict[str, str]:
        """Why this input cannot become an entry, as the form's errors; {} if it can."""
        try:
            validate_input(user_input)
        except InvalidHost:
            return {"base": "invalid_host"}
        # The same pump twice is an abort, before any other objection.
        await self.async_set_unique_id(entry_unique_id(user_input))
        self._abort_if_unique_id_configured()
        self._reservations()[self.flow_id] = str(
            user_input.get(CONF.DEVICE_POSTFIX, "")
        ).strip()
        reason = namespace_error(
            self.hass, user_input, pending=self._postfixes_of_other_flows()
        )
        if reason:
            return {"base": reason}
        if not await pump_answers(self.hass, user_input):
            return {"base": "cannot_connect"}
        # Once more after the probe: an entry may have appeared meanwhile, or
        # a reconfigure may have moved an existing one onto this endpoint.
        # Creating the entry then replaces it, taking its entities with it.
        self._abort_if_unique_id_configured()
        reason = namespace_error(self.hass, user_input)
        return {"base": reason} if reason else {}

    def _pumps(self) -> dict[str, config_entries.ConfigEntry]:
        """The pump entries a web interface can belong to, by entry id."""
        return {
            entry.entry_id: entry
            for entry in self.hass.config_entries.async_entries(CONST.DOMAIN)
            if not is_web_interface(entry)
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """A pump, or the web interface of a pump already set up."""
        if user_input is None and self._pumps():
            return self.async_show_menu(step_id="user", menu_options=["pump", "webif"])
        return await self._pump_form(user_input)

    async def async_step_pump(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """The pump's form, picked from the menu; it answers as the user step."""
        return await self._pump_form(user_input)

    async def _pump_form(
        self, user_input: dict[str, Any] | None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._objection(user_input)
        if user_input is not None and not errors:
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

    async def async_step_webif(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """A pump's web interface: which pump, and the login."""
        pumps = self._pumps()
        errors: dict[str, str] = {}
        if user_input is not None:
            pump = pumps.get(user_input[CONF.PUMP_ENTRY])
            if pump is None:
                return self.async_abort(reason="no_pump")
            await self.async_set_unique_id(f"{pump.entry_id}-{CONST.WEB_INTERFACE}")
            self._abort_if_unique_id_configured()
            try:
                pages = await read_web_interface(
                    self.hass,
                    pump.data[CONF.HOST],
                    user_input[CONF.USERNAME],
                    user_input[CONF.PASSWORD],
                )
            except WebifError as error:
                errors["base"] = web_interface_error(error)
            else:
                return self.async_create_entry(
                    title=f"{pump.title} web interface",
                    data={
                        CONF.KIND: CONST.WEB_INTERFACE,
                        CONF.PUMP_ENTRY: pump.entry_id,
                        CONF.USERNAME: user_input[CONF.USERNAME],
                        CONF.PASSWORD: user_input[CONF.PASSWORD],
                        CONF.PAGES: pages,
                    },
                )
        schema = vol.Schema(
            {
                vol.Required(CONF.PUMP_ENTRY): vol.In(
                    {entry_id: entry.title for entry_id, entry in pumps.items()}
                ),
                vol.Required(CONF.USERNAME): str,
                vol.Required(CONF.PASSWORD): PASSWORD_FIELD,
            }
        )
        return self.async_show_form(step_id="webif", data_schema=schema, errors=errors)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """The web interface refused the login it had."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """A new login for the web interface, checked on a short visit."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            pump = self.hass.config_entries.async_get_entry(entry.data[CONF.PUMP_ENTRY])
            if pump is None:
                return self.async_abort(reason="no_pump")
            try:
                pages = await read_web_interface(
                    self.hass,
                    pump.data[CONF.HOST],
                    user_input[CONF.USERNAME],
                    user_input[CONF.PASSWORD],
                )
            except WebifError as error:
                errors["base"] = web_interface_error(error)
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={**user_input, CONF.PAGES: pages},
                    reason="reauth_successful",
                )
        schema = vol.Schema(
            {
                vol.Required(CONF.USERNAME, default=entry.data[CONF.USERNAME]): str,
                vol.Required(CONF.PASSWORD): PASSWORD_FIELD,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Trigger a reconfiguration flow."""
        errors: dict[str, str] = {}
        self._reconfigure_entry = self._get_reconfigure_entry()
        if is_web_interface(self._reconfigure_entry):
            return self.async_abort(reason="webif_not_reconfigurable")

        # Pre-seed internal state dictionary with the current saved entry data
        if not self._stored_data:
            self._stored_data.update(self._reconfigure_entry.data)

        if user_input is not None:
            try:
                validate_input(user_input)
            except InvalidHost:
                errors["base"] = "invalid_host"
        if user_input is not None and not errors:
            if not await pump_answers(self.hass, user_input):
                errors["base"] = "cannot_connect"
        if user_input is not None and not errors:
            self._stored_data.update(user_input)
            new_unique_id = entry_unique_id(self._stored_data)
            holder = self.hass.config_entries.async_entry_for_domain_unique_id(
                CONST.DOMAIN, new_unique_id
            )
            # Home Assistant only logs a duplicate unique id on update; two
            # entries on one endpoint would poll and write the same pump.
            if (
                holder is not None
                and holder.entry_id != self._reconfigure_entry.entry_id
            ):
                return self.async_abort(reason="already_configured")
            # Not async_update_reload_and_abort: that schedules a reload of
            # its own while the update listener in __init__ reloads too.
            self.hass.config_entries.async_update_entry(
                self._reconfigure_entry,
                data=self._stored_data,
                unique_id=new_unique_id,
                title=self._stored_data[CONF.HOST],
            )
            return self.async_abort(reason="reconfigure_successful")

        # The user step's schema without prefix and postfix: every unique id
        # is built from them, so a change would orphan every entity's history
        # and start the write counters over. They are fixed at creation.
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
        errors: dict[str, str] = {}
        if user_input is not None:
            warning = user_input[CONST.OPTION_WRITE_WARNING_PER_DAY]
            limit = user_input[CONST.OPTION_WRITE_LIMIT_PER_DAY]
            # Writes stop at the limit, so a warning above it never fires.
            if 0 < limit < warning:
                errors["base"] = "warning_above_limit"
            else:
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
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)


class WebifOptionsFlow(config_entries.OptionsFlowWithReload):
    """How often the web interface's heat pump page is read.

    A change reloads the entry; there is no update listener for it.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Open on the web interface's own page."""
        return await self.async_step_webif(user_input)

    async def async_step_webif(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """The interval, in minutes."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(
            CONST.OPTION_WEBIF_INTERVAL, CONST.WEBIF_INTERVAL_MINUTES
        )
        schema = vol.Schema(
            {
                vol.Required(CONST.OPTION_WEBIF_INTERVAL, default=current): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=CONST.WEBIF_INTERVAL_MIN_MINUTES,
                        max=CONST.WEBIF_INTERVAL_MAX_MINUTES,
                    ),
                ),
            }
        )
        return self.async_show_form(step_id="webif", data_schema=schema)


class InvalidHost(exceptions.HomeAssistantError):
    """Error to indicate there is an invalid hostname."""
