"""The Update Coordinator for the ModbusItems."""

from collections.abc import Mapping
from datetime import timedelta
import logging
from typing import Any

from modbus_connection import ModbusError

from custom_components.weishaupt_modbus.weishaupt_modbus_api.const import (
    CIRCUIT_OFF,
    DEFAULT_WRITE_LIMIT_PER_DAY,
    DEFAULT_WRITE_WARNING_PER_DAY,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .configentry import MyConfigEntry
from .const import CONF, CONST, DeviceConstants
from .items import ModbusItem
from .weishaupt_modbus_api.device import WeishauptHeatPump
from .weishaupt_modbus_api.write_budget import WriteBudget

_LOGGER = logging.getLogger(__name__)

# Heating circuit -> the entry's switch for it; circuit 1 is always polled.
CIRCUIT_SWITCHES = {2: CONF.HK2, 3: CONF.HK3, 4: CONF.HK4, 5: CONF.HK5}
NOT_ENABLED_ISSUE = "circuit_not_enabled"
OFF_AT_CONTROLLER_ISSUE = "circuit_off_at_controller"

# A short outage keeps the last values; only a longer one takes every entity
# to unavailable. Counted from the first failed poll after a good one, so
# entities go unavailable on the fourth failed poll in a row - and never
# before the first refresh has produced values to keep.
FAILED_POLLS_TOLERATED = 3


def check_configured(modbus_item: ModbusItem, config_entry: MyConfigEntry) -> bool:
    """Whether the entry enables the circuit this item belongs to."""
    switches = {
        DeviceConstants.HZ2: CONF.HK2,
        DeviceConstants.HZ3: CONF.HK3,
        DeviceConstants.HZ4: CONF.HK4,
        DeviceConstants.HZ5: CONF.HK5,
    }
    switch = switches.get(modbus_item.device)
    return True if switch is None else bool(config_entry.data[switch])


def report_circuits(
    hass: HomeAssistant,
    entry: MyConfigEntry,
    configurations: Mapping[int, int | None],
) -> None:
    """Name each circuit the entry and the controller disagree on.

    Only a hint: the entry polls what it was set up to. One notice per
    circuit, so ignoring one says "not this circuit" and nothing more. A
    circuit the controller would not report on is judged neither way.
    """
    for circuit, switch in CIRCUIT_SWITCHES.items():
        setup = configurations.get(circuit)
        known = setup is not None
        set_up = known and setup != CIRCUIT_OFF
        enabled = bool(entry.data[switch])
        not_enabled = set_up and not enabled
        off_at_controller = known and not set_up and enabled
        _notice(hass, entry, NOT_ENABLED_ISSUE, circuit, not_enabled)
        _notice(hass, entry, OFF_AT_CONTROLLER_ISSUE, circuit, off_at_controller)


def clear_circuit_notices(hass: HomeAssistant, entry: MyConfigEntry) -> None:
    """Drop the entry's circuit notices; they name a pump that is gone."""
    for circuit in CIRCUIT_SWITCHES:
        for kind in (NOT_ENABLED_ISSUE, OFF_AT_CONTROLLER_ISSUE):
            ir.async_delete_issue(hass, CONST.DOMAIN, _issue_id(kind, entry, circuit))


def _notice(
    hass: HomeAssistant, entry: MyConfigEntry, kind: str, circuit: int, raised: bool
) -> None:
    issue_id = _issue_id(kind, entry, circuit)
    if not raised:
        ir.async_delete_issue(hass, CONST.DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        CONST.DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=kind,
        translation_placeholders={"circuit": str(circuit)},
    )


def _issue_id(kind: str, entry: MyConfigEntry, circuit: int) -> str:
    return f"{kind}_{entry.entry_id}_{circuit}"


def scan_interval(config_entry: MyConfigEntry) -> timedelta:
    """The poll interval from the entry's options, or the default."""
    seconds = config_entry.options.get(
        CONST.OPTION_SCAN_INTERVAL, CONST.SCAN_INTERVAL.total_seconds()
    )
    return timedelta(seconds=seconds)


def write_budget(config_entry: MyConfigEntry) -> WriteBudget:
    """The write counters with the thresholds from the entry's options.

    The day rolls over at local midnight, not UTC: that is when a user reads
    "writes today".
    """
    return WriteBudget(
        warn_at=config_entry.options.get(
            CONST.OPTION_WRITE_WARNING_PER_DAY, DEFAULT_WRITE_WARNING_PER_DAY
        ),
        limit=config_entry.options.get(
            CONST.OPTION_WRITE_LIMIT_PER_DAY, DEFAULT_WRITE_LIMIT_PER_DAY
        ),
        today=lambda: dt_util.now().date(),
    )


class WeishauptModbusCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the pump on the entry's interval and hands the rows to the entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        device: WeishauptHeatPump,
        api_items: list[ModbusItem],
        config_entry: MyConfigEntry,
    ) -> None:
        """Initialize the coordinator without synchronization overhead."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="weishaupt-modbus-coordinator",
            update_interval=scan_interval(config_entry),
            always_update=True,
        )
        self.device = device
        self.modbus_items = api_items
        self._failed_polls = 0
        self._traceback_logged = False

    @property
    def failed_polls(self) -> int:
        """Failed polls in a row since the last good one."""
        return self._failed_polls

    def get_value_from_item(self, translation_key: str) -> Any:
        """Read a value from another modbus item by its translation key."""
        for item in self.modbus_items:
            if item.translation_key == translation_key:
                return item.state
        return None

    async def _async_update_data(self) -> dict[str, Any]:
        """Read every band; a link problem is a failed refresh."""
        try:
            await self.device.async_update()
        except (TimeoutError, ModbusError) as err:
            # A band's time limit raises a bare TimeoutError, without a text.
            return self._failed_poll(str(err) or type(err).__name__, err)
        except Exception as err:
            # A fault of this code or of a library, not of the link. Counted
            # all the same: uncounted, it passed the grace polls, and Home
            # Assistant logged its traceback every poll. Its kind only, as
            # its text may hold the pump's address.
            if not self._traceback_logged:
                self._traceback_logged = True
                _LOGGER.exception("A poll failed on an unexpected error")
            return self._failed_poll(type(err).__name__, err)
        self._failed_polls = 0
        return self._results()

    def _failed_poll(self, reason: str, err: Exception) -> dict[str, Any]:
        """The last values while the grace polls last; UpdateFailed after."""
        self._failed_polls += 1
        if self.data is not None and self._failed_polls <= FAILED_POLLS_TOLERATED:
            # Debug only: the outage itself is logged once, by the base
            # class, when UpdateFailed takes the entities unavailable.
            _LOGGER.debug(
                "Poll failed (%d of %d tolerated), keeping the last values: %s",
                self._failed_polls,
                FAILED_POLLS_TOLERATED,
                reason,
            )
            return self.data
        raise UpdateFailed(
            translation_domain=CONST.DOMAIN,
            translation_key="communication_failed",
            translation_placeholders={"error": reason},
        ) from err

    def _results(self) -> dict[str, Any]:
        """The rows by translation key; a calculated sensor never gets a register value."""
        return {item.translation_key: item.state for item in self.modbus_items}
