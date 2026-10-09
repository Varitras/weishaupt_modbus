"""Keeping the web interface's pages fresh, and stopping when they will not come.

A round comes when the next page is due and asks for every page that is, the
one with the oldest reading first: after a timeout nothing else is asked in
that round, and a fixed order would leave the last pages starving on a
struggling pump. A page that fails keeps its last values through one failure,
the second in a row takes them away, and the third stops all polling until
the entry is reloaded.

It also writes the one setting the integration sets, the heating power
limit: once the requests have settled, between two pages of a round, never
beside one.
"""

import asyncio
from collections import Counter
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import timedelta
import logging
import math
import time
from typing import Any, NoReturn

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONST
from .webif import pages
from .webif.client import (
    Client,
    Closed,
    LoginRefused,
    MaybeSaved,
    NotSaved,
    Traffic,
    Unreachable,
    WebifError,
)
from .webif.discovery import HEAT_PUMP_PAGE, HEATING_PAGE, PAGE_MENUS, STATISTICS_PAGE
from .webif.setting import (
    FormMismatch,
    FormNotRead,
    NotReadBack,
    OtherValueShown,
    write_power_limit,
)
from .weishaupt_modbus_api.write_budget import WriteBudget

_LOGGER = logging.getLogger(__name__)

# User decision 2026-10-02: one failure is ridden out, the second in a row
# shows unavailable, the third stops polling.
FAILURES_KEPT = 1
FAILURES_TO_STOP = 3
# Home Assistant plans the next round from the whole second, so a round may
# start up to a second before a page's interval is over. Five cover that with
# room to spare and read a page at most 5 s early, nothing next to minutes.
DUE_SLACK_SECONDS = 5.0
# Home Assistant reads a wait of zero as "no polling", and a page can fall
# due just as a round asking another one ends: it gets the next round a
# second later instead.
SHORTEST_WAIT_SECONDS = 1.0
STOPPED_ISSUE = "webif_stopped"
# Spec decision, 2026-10-09: a write starts after this long without a further
# request, so arrow clicks and second thoughts make one write, of the last.
DEBOUNCE_SECONDS = 5.0
# Page key: the option holding its interval, and the default in minutes.
INTERVAL_OPTIONS = {
    HEAT_PUMP_PAGE: (
        CONST.OPTION_WEBIF_HEAT_PUMP_INTERVAL,
        CONST.WEBIF_INTERVAL_MINUTES,
    ),
    STATISTICS_PAGE: (
        CONST.OPTION_WEBIF_STATISTICS_INTERVAL,
        CONST.WEBIF_SLOW_INTERVAL_MINUTES,
    ),
    HEATING_PAGE: (
        CONST.OPTION_WEBIF_HEATING_INTERVAL,
        CONST.WEBIF_SLOW_INTERVAL_MINUTES,
    ),
}

Values = dict[str, str]


@dataclass(frozen=True)
class Page:
    """A page to keep fresh: where it is, what it must show, how often.

    A menu page is read for the values its entries show one level down.
    """

    key: str
    path: str
    required: frozenset[str]
    interval: timedelta
    menu: bool = False

    def read(self, text: str) -> list[tuple[str, str]]:
        """The titles and values the page shows."""
        if self.menu:
            return [
                (entry.title, entry.text) for entry in pages.children(text, self.path)
            ]
        return pages.values(text)

    def is_whole(self, text: str) -> bool:
        """Every required title is there once, and no value is left blank."""
        return pages.is_complete(self.read(text), self.required)

    def missing(self, text: str) -> frozenset[str]:
        """The required titles the page does not show."""
        return self.required - {title for title, _ in self.read(text)}

    def unclear(self, text: str) -> frozenset[str]:
        """The titles shown without a value, and the required ones shown twice."""
        found = self.read(text)
        counts = Counter(title for title, _ in found)
        empty = {title for title, shown in found if not shown}
        twice = {title for title in self.required if counts[title] > 1}
        return frozenset(empty | twice)


def polled_pages(
    paths: Mapping[str, str],
    options: Mapping[str, Any],
    required: Mapping[str, frozenset[str]],
) -> list[Page]:
    """The pages at `paths`, each on the interval its option sets.

    `required` are the titles each page must show, those of its sensors.
    """

    def every(key: str) -> timedelta:
        option, default = INTERVAL_OPTIONS[key]
        return timedelta(minutes=options.get(option, default))

    return [
        Page(
            HEAT_PUMP_PAGE,
            paths[HEAT_PUMP_PAGE],
            required[HEAT_PUMP_PAGE],
            every(HEAT_PUMP_PAGE),
        ),
        Page(
            STATISTICS_PAGE,
            paths[STATISTICS_PAGE],
            required[STATISTICS_PAGE],
            every(STATISTICS_PAGE),
        ),
        Page(
            HEATING_PAGE,
            paths[HEATING_PAGE],
            required[HEATING_PAGE],
            every(HEATING_PAGE),
            menu=True,
        ),
    ]


@dataclass
class _Reading:
    values: Values | None = None
    read_at: float | None = None
    asked_at: float | None = None
    failures: int = 0


def _oldest_first(reading: _Reading) -> float:
    return -math.inf if reading.read_at is None else reading.read_at


class WriteFailed(WebifError):
    """A write ended in a fault of this code or of a library; it is logged."""


@dataclass
class _Burst:
    """Requests not written yet: the last target, and the outcome they share.

    It takes requests until its write starts, also while that write waits
    for one still running.
    """

    target: int
    outcome: asyncio.Future[None]


def _cut_off(*, counted: bool) -> WebifError:
    """What the callers of a write cancelled with its entry hear."""
    if counted:
        # The save may have gone out: "called off" would invite a second one.
        return MaybeSaved("cancelled after the save went out")
    return Closed("the entry unloads")


def _retrieved(outcome: asyncio.Future[None]) -> None:
    # Callers that gave up leave a failed write's outcome unread, and asyncio
    # would log it as an error nobody handled.
    if not outcome.cancelled():
        outcome.exception()


def _raise_translated(error: Exception) -> NoReturn:
    """The way a write ended, as its callers are told it."""
    match error:
        case HomeAssistantError():
            raise error
        case FormNotRead():
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_write_form_not_read",
            ) from error
        case FormMismatch():
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_write_form_mismatch",
            ) from error
        case NotSaved():
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN, translation_key="webif_write_not_saved"
            ) from error
        case MaybeSaved():
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_write_maybe_saved",
            ) from error
        case OtherValueShown():
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_write_other_value",
                translation_placeholders={
                    "shown": str(error.shown),
                    "target": str(error.target),
                },
            ) from error
        case NotReadBack():
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_write_not_read_back",
            ) from error
        case LoginRefused():
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_write_login_refused",
            ) from error
        case Closed():
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN, translation_key="webif_write_aborted"
            ) from error
    raise HomeAssistantError(
        translation_domain=CONST.DOMAIN, translation_key="webif_write_failed"
    ) from error


def _seconds_since(read_at: float | None, now: float) -> int | None:
    if read_at is None:
        return None
    return round(now - read_at)


class WebifCoordinator(DataUpdateCoordinator[dict[str, Values | None]]):
    """Each page's last whole values by page key; None where they are gone."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: Client,
        polled: list[Page],
        *,
        budget: WriteBudget,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Each round plans the next for when the next page is due.

        `budget` counts and limits the settings written.
        """
        shortest = min(page.interval for page in polled)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="weishaupt-webif",
            update_interval=shortest,
        )
        self._shortest_interval = shortest
        self._client = client
        self._pages = polled
        self.budget = budget
        self._readings = {page.key: _Reading() for page in polled}
        self._clock = clock
        self._stopped_by: tuple[str, str] | None = None
        self._refused: LoginRefused | None = None
        self._traceback_logged = False
        # A write's own: its message sends the user to the log, also after a
        # round's traceback was logged.
        self._write_traceback_logged = False
        self._no_dialog_visit = asyncio.Event()
        self._no_dialog_visit.set()
        # Held for a round's page and for a whole write: a round request
        # waiting on the client got in between a write's own requests.
        self._asking = asyncio.Lock()
        self._burst: _Burst | None = None
        self._quiet: asyncio.TimerHandle | None = None
        self._issue = f"{STOPPED_ISSUE}_{config_entry.entry_id}"
        # The slowest answer of the last round that asked anything.
        self.answer_seconds: float | None = None
        # Reloading the entry is how polling resumes after a stop.
        ir.async_delete_issue(hass, CONST.DOMAIN, self._issue)

    @asynccontextmanager
    async def dialog_visiting(self) -> AsyncIterator[None]:
        """Hold the rounds from the next page on while a dialog of this entry visits.

        Both share the gap between requests: live, the rounds took 6 of the
        18 requests of a reconfigure, and the dialog waited 134 s instead of
        about 90. After a visit that works out the entry reloads anyway.
        """
        self._no_dialog_visit.clear()
        try:
            yield
        finally:
            self._no_dialog_visit.set()

    @property
    def _heating(self) -> Page:
        return next(page for page in self._pages if page.key == HEATING_PAGE)

    async def async_shutdown(self) -> None:
        """Stop, and take a stop notice along: the entry it names is unloading.

        A write still waiting for its requests to settle is called off.
        """
        await super().async_shutdown()
        if self._quiet is not None:
            self._quiet.cancel()
            self._quiet = None
        if self._burst is not None:
            self._burst.outcome.set_exception(Closed("the entry unloads"))
            self._burst = None
        ir.async_delete_issue(self.hass, CONST.DOMAIN, self._issue)

    async def set_power_limit(self, target: int) -> None:
        """Write the heating power limit once the requests have settled.

        Requests less than DEBOUNCE_SECONDS apart make one write, of the last
        target, and each of them gets its outcome. Raises a translated
        HomeAssistantError when the write is refused or fails.
        """
        self._refuse_write()
        if self._burst is None:
            self._burst = _Burst(target, self.hass.loop.create_future())
            self._burst.outcome.add_done_callback(_retrieved)
        burst = self._burst
        burst.target = target
        if self._quiet is not None:
            self._quiet.cancel()
        self._quiet = self.hass.loop.call_later(
            DEBOUNCE_SECONDS, self._start_write, burst
        )
        try:
            await asyncio.shield(burst.outcome)
        except (WebifError, HomeAssistantError) as error:
            _raise_translated(error)

    def _refuse_write(self) -> None:
        """Refuse a write that cannot go out, before any request."""
        if self._stopped_by is not None:
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN, translation_key="webif_write_stopped"
            )
        if self._refused is not None:
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_write_login_refused",
            )
        if not self.budget.allows_write():
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_write_limit_reached",
                translation_placeholders={"limit": str(self.budget.limit)},
            )

    @callback
    def _start_write(self, burst: _Burst) -> None:
        self._quiet = None
        self.config_entry.async_create_background_task(
            self.hass, self._write(burst), "weishaupt-webif power limit"
        )

    async def _write(self, burst: _Burst) -> None:
        """One write, its outcome handed to every caller that asked for it."""
        outcome = burst.outcome
        counted = False

        def count_write() -> None:
            nonlocal counted
            counted = True
            self.budget.record_write()

        try:
            async with self._asking:
                # Live a write takes 20 to 60 s: the requests made while
                # another ran joined this one, and from here on they wait
                # for a write of their own.
                if self._burst is burst:
                    self._burst = None
                if outcome.done():
                    # Written by a write started for it earlier, or called
                    # off by the unload while it waited for its turn.
                    return
                self._refuse_write()
                text = await write_power_limit(
                    self._client,
                    self._heating.path,
                    self._heating.is_whole,
                    burst.target,
                    count_write,
                )
                self._publish(self._heating, text)
        except (WebifError, HomeAssistantError) as error:
            self._after_failed_write(error)
            outcome.set_exception(error)
        except Exception as error:
            # A fault of this code or of a library, not of the pump.
            if not self._write_traceback_logged:
                self._write_traceback_logged = True
                _LOGGER.exception(
                    "Writing the power limit failed on an unexpected error"
                )
            outcome.set_exception(WriteFailed(type(error).__name__))
        else:
            outcome.set_result(None)
        finally:
            # Cancelled as the entry unloads: no caller may wait for good.
            if not outcome.done():
                outcome.set_exception(_cut_off(counted=counted))

    def _after_failed_write(self, error: Exception) -> None:
        if isinstance(error, OtherValueShown):
            self._publish(self._heating, error.text)
        elif isinstance(error, (MaybeSaved, NotReadBack)):
            # Unknown what the controller holds: the next round asks.
            self._readings[HEATING_PAGE].asked_at = None
        elif isinstance(error, LoginRefused):
            self._refused = error

    def _publish(self, page: Page, text: str) -> None:
        """A page a write read counts as that page's reading, as of now."""
        self._store(page, text)
        self._readings[page.key].asked_at = self._readings[page.key].read_at
        # Home Assistant restarts its refresh timer here, with the wait the
        # last round planned: the next page due would come that much late.
        self._plan_next_round(True)
        self.async_set_updated_data(self._published())

    @property
    def traffic(self) -> Traffic:
        """What the client has asked of the controller so far."""
        return self._client.traffic

    def diagnostics(self) -> dict[str, Any]:
        """The polling's own state, for the diagnostics download."""
        now = self._clock()
        return {
            "answer_seconds": self.answer_seconds,
            "traffic": asdict(self.traffic),
            "stopped_by": self._stopped_by,
            "login_refused": self._refused is not None,
            "page_states": {
                key: {
                    "failures": reading.failures,
                    "seconds_since_read": _seconds_since(reading.read_at, now),
                }
                for key, reading in self._readings.items()
            },
        }

    async def _async_update_data(self) -> dict[str, Values | None]:
        if self._refused is not None:
            # Until the reauth reloads the entry: an update requested by hand
            # would send the refused login again.
            raise ConfigEntryAuthFailed(
                translation_domain=CONST.DOMAIN, translation_key="webif_login_refused"
            ) from self._refused
        if self._stopped_by is None:
            finished = await self._round()
            self._plan_next_round(finished)
        if (slowest := self._client.take_slowest_answer()) is not None:
            self.answer_seconds = slowest
        if self._stopped_by is not None:
            page_name, error = self._stopped_by
            # The exception's own text, of the same name as the issue's; a
            # literal, as the translation check reads it from the source.
            raise UpdateFailed(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_stopped",
                translation_placeholders={"page": page_name, "error": error},
            )
        return self._published()

    def _published(self) -> dict[str, Values | None]:
        return {
            key: reading.values if reading.failures <= FAILURES_KEPT else None
            for key, reading in self._readings.items()
        }

    async def _round(self) -> bool:
        """Ask every due page; False when the round had to end early."""
        for page in self._due():
            await self._no_dialog_visit.wait()
            async with self._asking:
                fetched = await self._fetch(page)
            if not fetched:
                return False
        return True

    def _plan_next_round(self, finished: bool) -> None:
        # Home Assistant plans the next round from the end of every refresh,
        # one requested by hand included: on a fixed interval, an update just
        # before a page was due put that page off by a whole round.
        if self._stopped_by is not None:
            return
        now = self._clock()
        wait = max(
            min(self._due_in(page, now) for page in self._pages),
            SHORTEST_WAIT_SECONDS,
        )
        if not finished:
            # The pages the round did not reach are due at once; the
            # struggling controller gets the shortest interval to recover.
            wait = max(wait, self._shortest_interval.total_seconds())
        self.update_interval = timedelta(seconds=wait)

    def _due(self) -> list[Page]:
        now = self._clock()
        due = [
            page for page in self._pages if self._due_in(page, now) <= DUE_SLACK_SECONDS
        ]
        return sorted(due, key=lambda page: _oldest_first(self._readings[page.key]))

    def _due_in(self, page: Page, now: float) -> float:
        # From the last try, not the last good reading: a failed page waits out
        # its interval too, also for an update requested by hand.
        asked_at = self._readings[page.key].asked_at
        if asked_at is None:
            return 0.0
        return asked_at + page.interval.total_seconds() - now

    async def _fetch(self, page: Page) -> bool:
        """Read one page; False when the round has to end."""
        reading = self._readings[page.key]
        reading.asked_at = self._clock()
        try:
            text = await self._client.page(page.path, page.is_whole)
        except LoginRefused as error:
            # Stops polling at once: no wrong login is repeated.
            self._refused = error
            raise ConfigEntryAuthFailed(
                translation_domain=CONST.DOMAIN, translation_key="webif_login_refused"
            ) from error
        except Closed:
            # The entry unloads while a round runs: no failure of the page,
            # and nothing to ask any more.
            return False
        except WebifError as error:
            if not self._failed(page, str(error)):
                return False
            # A struggling server gets no further request in this round.
            return not isinstance(error, Unreachable)
        except Exception as error:
            # A fault of this code or of a library, not of the pump. Counted
            # all the same: uncounted, it passed the brake, and Home Assistant
            # logged its traceback every round. The stop names its kind only,
            # as its text may hold the pump's address.
            if not self._traceback_logged:
                self._traceback_logged = True
                _LOGGER.exception("%s failed on an unexpected error", page.key)
            return self._failed(page, type(error).__name__)
        self._store(page, text)
        return True

    def _store(self, page: Page, text: str) -> None:
        reading = self._readings[page.key]
        # Only what a sensor reads: the rest of a page would go into the
        # diagnostics download, an identifying entry among it maybe.
        reading.values = {
            title: shown for title, shown in page.read(text) if title in page.required
        }
        reading.read_at = self._clock()
        reading.failures = 0

    def _failed(self, page: Page, reason: str) -> bool:
        """Count a failure of the page; False when it stopped the polling."""
        reading = self._readings[page.key]
        reading.failures += 1
        _LOGGER.debug("%s failed, %d in a row: %s", page.key, reading.failures, reason)
        if reading.failures < FAILURES_TO_STOP:
            return True
        self._stop(page, reason)
        return False

    def _stop(self, page: Page, reason: str) -> None:
        self._stopped_by = (PAGE_MENUS[page.key], reason)
        self.update_interval = None
        ir.async_create_issue(
            self.hass,
            CONST.DOMAIN,
            self._issue,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=STOPPED_ISSUE,
            translation_placeholders={
                "page": PAGE_MENUS[page.key],
                "error": reason,
            },
        )
