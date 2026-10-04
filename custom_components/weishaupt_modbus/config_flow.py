"""Config flow."""

import asyncio
from collections.abc import Coroutine, Mapping
import logging
from pathlib import Path
from typing import Any

from modbus_connection import ModbusError, ModbusTcpParams
import voluptuous as vol

from homeassistant import config_entries, exceptions
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .configentry import host_lock, is_web_interface, web_interface_title
from .const import CONF, CONST
from .kennfeld import get_filepath
from .migrate_helpers import entry_unique_id
from .webif.client import LoginRefused, Unreachable, WebifError
from .webif.discovery import DoubledMenuEntries, MissingMenuEntries
from .webif_coordinator import INTERVAL_OPTIONS
from .webif_visit import MissingTitles, UnclearValues, UnknownUnits, read_web_interface
from .weishaupt_modbus_api.const import (
    DEFAULT_PORT,
    DEFAULT_WRITE_LIMIT_PER_DAY,
    DEFAULT_WRITE_WARNING_PER_DAY,
    EEPROM_WRITE_RATING,
    MODBUS_UNIT_ID,
)

_LOGGER = logging.getLogger(__name__)


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
        # The web interface may be asking the same controller right now.
        async with (
            host_lock(hass, data[CONF.HOST]),
            async_get_temporary_unit(hass, params, MODBUS_UNIT_ID) as unit,
        ):
            await unit.read_input_registers(PROBE_REGISTER, 1)
    except ModbusError, OSError, TimeoutError:
        return False
    return True


async def _probe_error(hass: HomeAssistant, data: dict[str, Any]) -> str | None:
    """The form's error for the probe of the heat pump; None when it answers."""
    try:
        answers = await pump_answers(hass, data)
    except Exception:
        # A fault of this code: Home Assistant would end the dialog in a bare
        # "Unknown error", with no form to try again in and a log line that
        # does not say it was the probe.
        _LOGGER.exception("The heat pump probe failed on an unexpected error")
        return "unknown"
    if not answers:
        return "cannot_connect"
    return None


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


def web_interface_error(error: WebifError) -> str:
    """The form's error for a visit that failed."""
    if isinstance(error, LoginRefused):
        return "invalid_auth"
    if isinstance(error, Unreachable):
        return "cannot_connect"
    if isinstance(error, MissingTitles):
        return "missing_titles"
    if isinstance(error, UnclearValues):
        return "unclear_values"
    if isinstance(error, UnknownUnits):
        return "unknown_units"
    if isinstance(error, MissingMenuEntries):
        return "missing_menu_entries"
    if isinstance(error, DoubledMenuEntries):
        return "doubled_menu_entries"
    return "cannot_read"


def web_interface_error_placeholders(error: WebifError) -> dict[str, str]:
    """The placeholders of the form's error for a visit that failed."""
    if isinstance(error, MissingTitles | UnclearValues | UnknownUnits):
        return {"page": error.page, "titles": ", ".join(sorted(error.titles))}
    if isinstance(error, MissingMenuEntries | DoubledMenuEntries):
        return {"titles": ", ".join(sorted(error.titles))}
    return {}


async def holding_its_rounds[T](
    entry: config_entries.ConfigEntry | None, visit: Coroutine[Any, Any, T]
) -> T:
    """A dialog's visit, with the rounds of the running entry it is for held.

    Both keep the gap together, and a dialog that works out reloads the
    entry anyway.
    """
    if entry is None or entry.state is not config_entries.ConfigEntryState.LOADED:
        return await visit
    async with entry.runtime_data.coordinator.dialog_visiting():
        return await visit


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
        self._visit: asyncio.Task[dict[str, str]] | None = None
        self._visit_login: dict[str, Any] = {}
        self._after_visit = ""

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
        if error := await _probe_error(self.hass, user_input):
            return {"base": error}
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

    def _pumps_without_web_interface(self) -> dict[str, config_entries.ConfigEntry]:
        """The pumps the dialog offers: each has one web interface at most."""
        taken = {
            entry.data[CONF.PUMP_ENTRY]
            for entry in self.hass.config_entries.async_entries(CONST.DOMAIN)
            if is_web_interface(entry)
        }
        return {
            entry_id: pump
            for entry_id, pump in self._pumps().items()
            if entry_id not in taken
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
        if (visit := self._visit) is not None:
            return self._webif_visited(pumps, visit)
        if not pumps:
            return self.async_abort(reason="no_pump")
        offered = self._pumps_without_web_interface()
        if not offered:
            return self.async_abort(reason="every_pump_has_web_interface")
        if user_input is None:
            return self._webif_form(offered, {})
        pump = pumps.get(user_input[CONF.PUMP_ENTRY])
        if pump is None:
            return self.async_abort(reason="no_pump")
        await self.async_set_unique_id(f"{pump.entry_id}-{CONST.WEB_INTERFACE}")
        if pump.entry_id not in offered:
            return self.async_abort(reason="web_interface_exists")
        return await self._visit_web_interface(pump, user_input, next_step_id="webif")

    def _webif_visited(
        self,
        pumps: dict[str, config_entries.ConfigEntry],
        visit: asyncio.Task[dict[str, str]],
    ) -> config_entries.ConfigFlowResult:
        """The new entry with the pages the visit found, or the form again."""
        pages, errors, placeholders = self._visit_result(visit)
        login = self._visit_login
        pump = pumps.get(login[CONF.PUMP_ENTRY])
        if pump is None:
            return self.async_abort(reason="no_pump")
        if pages is None:
            return self._webif_form(
                self._pumps_without_web_interface(), errors, placeholders
            )
        return self.async_create_entry(
            title=web_interface_title(pump.title),
            data={
                CONF.KIND: CONST.WEB_INTERFACE,
                CONF.PUMP_ENTRY: pump.entry_id,
                CONF.USERNAME: login[CONF.USERNAME],
                CONF.PASSWORD: login[CONF.PASSWORD],
                CONF.PAGES: pages,
                CONF.PUMP_TITLE: pump.title,
            },
        )

    def _webif_form(
        self,
        pumps: dict[str, config_entries.ConfigEntry],
        errors: dict[str, str],
        placeholders: dict[str, str] | None = None,
    ) -> config_entries.ConfigFlowResult:
        schema = vol.Schema(
            {
                vol.Required(CONF.PUMP_ENTRY): vol.In(
                    {entry_id: entry.title for entry_id, entry in pumps.items()}
                ),
                vol.Required(CONF.USERNAME): str,
                vol.Required(CONF.PASSWORD): PASSWORD_FIELD,
            }
        )
        # After a failed visit the pump and the user come back; the password not.
        typed = {
            key: self._visit_login[key]
            for key in (CONF.PUMP_ENTRY, CONF.USERNAME)
            if key in self._visit_login
        }
        return self.async_show_form(
            step_id="webif",
            data_schema=self.add_suggested_values_to_schema(schema, typed),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _visit_web_interface(
        self,
        pump: config_entries.ConfigEntry,
        login: dict[str, Any],
        *,
        next_step_id: str,
        entry: config_entries.ConfigEntry | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Start the short visit; the dialog shows its progress meanwhile.

        `entry` is the web interface entry a new login is for, if any.
        """
        self._visit_login = login
        self._after_visit = next_step_id
        visit = read_web_interface(
            self.hass,
            pump.data[CONF.HOST],
            login[CONF.USERNAME],
            login[CONF.PASSWORD],
        )
        self._visit = self.hass.async_create_task(holding_its_rounds(entry, visit))
        return await self.async_step_webif_visit()

    async def async_step_webif_visit(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """The visit runs; Home Assistant calls this again once it is done."""
        if self._visit is not None and not self._visit.done():
            return self.async_show_progress(
                step_id="webif_visit",
                progress_action="webif_visit",
                progress_task=self._visit,
            )
        return self.async_show_progress_done(next_step_id=self._after_visit)

    def _visit_result(
        self, visit: asyncio.Task[dict[str, str]]
    ) -> tuple[dict[str, str] | None, dict[str, str], dict[str, str]]:
        """The pages the finished visit found, or the form's error and its placeholders."""
        self._visit = None
        try:
            return visit.result(), {}, {}
        except WebifError as error:
            _LOGGER.debug("Web interface visit failed: %s", error)
            errors = {"base": web_interface_error(error)}
            return None, errors, web_interface_error_placeholders(error)
        except Exception:
            # As in the probe: a fault of this code, not of the web interface.
            _LOGGER.exception("The web interface visit failed on an unexpected error")
            return None, {"base": "unknown"}, {}

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """The web interface refused the login it had."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """A new login for the web interface it refused."""
        return await self._web_interface_login(
            self._get_reauth_entry(),
            user_input,
            step_id="reauth_confirm",
            reason="reauth_successful",
        )

    async def async_step_reconfigure_webif(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """A new login for the web interface, and its pages searched again."""
        return await self._web_interface_login(
            self._get_reconfigure_entry(),
            user_input,
            step_id="reconfigure_webif",
            reason="reconfigure_successful",
        )

    async def _web_interface_login(
        self,
        entry: config_entries.ConfigEntry,
        user_input: dict[str, Any] | None,
        *,
        step_id: str,
        reason: str,
    ) -> config_entries.ConfigFlowResult:
        """Check the login on a short visit; the pages it finds replace the old."""
        if (visit := self._visit) is not None:
            pages, errors, placeholders = self._visit_result(visit)
            if pages is None:
                return self._login_form(entry, step_id, errors, placeholders)
            return self.async_update_reload_and_abort(
                entry,
                data_updates={**self._visit_login, CONF.PAGES: pages},
                reason=reason,
            )
        if user_input is None:
            return self._login_form(entry, step_id, {})
        pump = self.hass.config_entries.async_get_entry(entry.data[CONF.PUMP_ENTRY])
        if pump is None:
            return self.async_abort(reason="pump_removed")
        return await self._visit_web_interface(
            pump, user_input, next_step_id=step_id, entry=entry
        )

    def _login_form(
        self,
        entry: config_entries.ConfigEntry,
        step_id: str,
        errors: dict[str, str],
        placeholders: dict[str, str] | None = None,
    ) -> config_entries.ConfigFlowResult:
        user = self._visit_login.get(CONF.USERNAME, entry.data[CONF.USERNAME])
        schema = vol.Schema(
            {
                vol.Required(CONF.USERNAME, default=user): str,
                vol.Required(CONF.PASSWORD): PASSWORD_FIELD,
            }
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Trigger a reconfiguration flow."""
        errors: dict[str, str] = {}
        self._reconfigure_entry = self._get_reconfigure_entry()
        if is_web_interface(self._reconfigure_entry):
            return await self.async_step_reconfigure_webif()

        # Pre-seed internal state dictionary with the current saved entry data
        if not self._stored_data:
            self._stored_data.update(self._reconfigure_entry.data)

        if user_input is not None:
            try:
                validate_input(user_input)
            except InvalidHost:
                errors["base"] = "invalid_host"
        if user_input is not None and not errors:
            if error := await _probe_error(self.hass, user_input):
                errors["base"] = error
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
    """How often each of the web interface's pages is read.

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
        """The intervals, in minutes."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        # A plain range this short draws a slider that hides its value.
        minutes = vol.All(
            NumberSelector(
                NumberSelectorConfig(
                    min=CONST.WEBIF_INTERVAL_MIN_MINUTES,
                    max=CONST.WEBIF_INTERVAL_MAX_MINUTES,
                    step=1,
                    mode=NumberSelectorMode.SLIDER,
                    unit_of_measurement=UnitOfTime.MINUTES,
                )
            ),
            vol.Coerce(int),
        )
        schema = vol.Schema(
            {
                vol.Required(option, default=options.get(option, default)): minutes
                for option, default in INTERVAL_OPTIONS.values()
            }
        )
        return self.async_show_form(step_id="webif", data_schema=schema)


class InvalidHost(exceptions.HomeAssistantError):
    """Error to indicate there is an invalid hostname."""
