"""Keeping the web interface's pages fresh, and stopping when they will not come.

A round asks for the pages that are due, the one with the oldest reading
first: after a timeout nothing else is asked in that round, and a fixed order
would leave the last pages starving on a struggling pump. A page that fails
keeps its last values through one failure, the second in a row takes them
away, and the third stops all polling until the entry is reloaded.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
import logging
import math
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONST
from .webif import pages
from .webif.client import Client, LoginRefused, Unreachable, WebifError

_LOGGER = logging.getLogger(__name__)

# User decision 2026-10-02: one failure is ridden out, the second in a row
# shows unavailable, the third stops polling.
FAILURES_KEPT = 1
FAILURES_TO_STOP = 3
# Home Assistant plans the next round from the whole second, so a round may
# start up to a second before a page's interval is over.
DUE_SLACK_SECONDS = 5.0
STOPPED_ISSUE = "webif_stopped"

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
        """Every required title is there, and no value is left blank."""
        return pages.is_complete(self.read(text), self.required)


@dataclass
class _Reading:
    values: Values | None = None
    read_at: float | None = None
    failures: int = 0


def _oldest_first(reading: _Reading) -> float:
    return -math.inf if reading.read_at is None else reading.read_at


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
        """A round as often as the most frequent page is due."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="weishaupt-webif",
            update_interval=min(page.interval for page in polled),
        )
        self._client = client
        self._pages = polled
        self._readings = {page.key: _Reading() for page in polled}
        self._clock = clock
        self._stopped_by: tuple[str, str] | None = None
        self._issue = f"{STOPPED_ISSUE}_{config_entry.entry_id}"
        # Reloading the entry is how polling resumes after a stop.
        ir.async_delete_issue(hass, CONST.DOMAIN, self._issue)

    async def _async_update_data(self) -> dict[str, Values | None]:
        if self._stopped_by is None:
            for page in self._due():
                if not await self._fetch(page):
                    break
        if self._stopped_by is not None:
            page_key, error = self._stopped_by
            raise UpdateFailed(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_stopped",
                translation_placeholders={"page": page_key, "error": error},
            )
        return {
            key: reading.values if reading.failures <= FAILURES_KEPT else None
            for key, reading in self._readings.items()
        }

    def _due(self) -> list[Page]:
        now = self._clock()
        due = [page for page in self._pages if self._is_due(page, now)]
        return sorted(due, key=lambda page: _oldest_first(self._readings[page.key]))

    def _is_due(self, page: Page, now: float) -> bool:
        read_at = self._readings[page.key].read_at
        return (
            read_at is None
            or now - read_at >= page.interval.total_seconds() - DUE_SLACK_SECONDS
        )

    async def _fetch(self, page: Page) -> bool:
        """Read one page; False when the round has to end."""
        reading = self._readings[page.key]
        try:
            text = await self._client.page(page.path, page.is_whole)
        except LoginRefused as error:
            # Stops polling at once: no wrong login is repeated.
            raise ConfigEntryAuthFailed(str(error)) from error
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
        reading.values = dict(page.read(text))
        reading.read_at = self._clock()
        reading.failures = 0
        return True

    def _stop(self, page: Page, error: WebifError) -> None:
        self._stopped_by = (page.key, str(error))
        self.update_interval = None
        ir.async_create_issue(
            self.hass,
            CONST.DOMAIN,
            self._issue,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=STOPPED_ISSUE,
            translation_placeholders={"page": page.key, "error": str(error)},
        )
