"""Keeping the web interface's pages fresh, and stopping when they will not come.

A round comes when the next page is due and asks for every page that is, the
one with the oldest reading first: after a timeout nothing else is asked in
that round, and a fixed order would leave the last pages starving on a
struggling pump. A page that fails keeps its last values through one failure,
the second in a row takes them away, and the third stops all polling until
the entry is reloaded.
"""

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import timedelta
import logging
import math
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONST
from .webif import pages
from .webif.client import Client, Closed, LoginRefused, Traffic, Unreachable, WebifError
from .webif.discovery import HEAT_PUMP_PAGE, HEATING_PAGE, PAGE_MENUS, STATISTICS_PAGE

_LOGGER = logging.getLogger(__name__)

# User decision 2026-10-02: one failure is ridden out, the second in a row
# shows unavailable, the third stops polling.
FAILURES_KEPT = 1
FAILURES_TO_STOP = 3
# Home Assistant plans the next round from the whole second, so a round may
# start up to a second before a page's interval is over. Five cover that with
# room to spare and read a page at most 5 s early, nothing next to minutes.
DUE_SLACK_SECONDS = 5.0
STOPPED_ISSUE = "webif_stopped"
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


def _seconds_since(read_at: float | None, now: float) -> int | None:
    if read_at is None:
        return None
    return round(now - read_at)


class WebifCoordinator(DataUpdateCoordinator[dict[str, Values | None]]):
    """Each page's last whole values by page key; None where they are gone."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: Client,
        polled: list[Page],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Each round plans the next for when the next page is due."""
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
        self._readings = {page.key: _Reading() for page in polled}
        self._clock = clock
        self._stopped_by: tuple[str, str] | None = None
        self._refused: LoginRefused | None = None
        self._issue = f"{STOPPED_ISSUE}_{config_entry.entry_id}"
        # The slowest answer of the last round that asked anything.
        self.answer_seconds: float | None = None
        # Reloading the entry is how polling resumes after a stop.
        ir.async_delete_issue(hass, CONST.DOMAIN, self._issue)

    async def async_shutdown(self) -> None:
        """Stop, and take a stop notice along: the entry it names is unloading."""
        await super().async_shutdown()
        ir.async_delete_issue(self.hass, CONST.DOMAIN, self._issue)

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
        return {
            key: reading.values if reading.failures <= FAILURES_KEPT else None
            for key, reading in self._readings.items()
        }

    async def _round(self) -> bool:
        """Ask every due page; False when the round had to end early."""
        for page in self._due():
            if not await self._fetch(page):
                return False
        return True

    def _plan_next_round(self, finished: bool) -> None:
        # Home Assistant plans the next round from the end of every refresh,
        # one requested by hand included: on a fixed interval, an update just
        # before a page was due put that page off by a whole round.
        if self._stopped_by is not None:
            return
        now = self._clock()
        wait = min(self._due_in(page, now) for page in self._pages)
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
            reading.failures += 1
            _LOGGER.debug(
                "%s failed, %d in a row: %s", page.key, reading.failures, error
            )
            if reading.failures >= FAILURES_TO_STOP:
                self._stop(page, error)
                return False
            # A struggling server gets no further request in this round.
            return not isinstance(error, Unreachable)
        # Only what a sensor reads: the rest of a page would go into the
        # diagnostics download, an identifying entry among it maybe.
        reading.values = {
            title: shown for title, shown in page.read(text) if title in page.required
        }
        reading.read_at = self._clock()
        reading.failures = 0
        return True

    def _stop(self, page: Page, error: WebifError) -> None:
        self._stopped_by = (PAGE_MENUS[page.key], str(error))
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
                "error": str(error),
            },
        )
