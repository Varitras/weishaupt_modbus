"""Keeping the web interface's pages fresh, and stopping when they will not come.

A round comes when the next page is due and asks for every page that is, the
one with the oldest reading first: after a timeout nothing else is asked in
that round, and a fixed order would leave the last pages starving on a
struggling pump. A page that fails keeps its last values through one failure,
the second in a row takes them away, and the third stops all polling. Then
only that page is asked again, at waits growing to two hours, until it comes
whole or the entry is reloaded.

A page whose address brings the overview instead, the controller having
given its menus new codes, sets off a search for all pages from that
overview, at most once a round; the addresses found are kept in the entry.
A page not found fails on its own, and a stop by it asks for a reconfigure.

It also writes the settings the integration sets, the heating power limit,
an Evoblock's minimum flow temperature of each circuit and the hot water
modulation: once
the requests for one have settled, between two pages of a round, never
beside one.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, replace
from datetime import timedelta
from functools import partial
import logging
import math
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF, CONST
from .outage import ExpectedOutage, log_expected_outages_at_info
from .webif.client import (
    Broken,
    Client,
    Closed,
    LoginRefused,
    MaybeSaved,
    Traffic,
    Unreachable,
    WebifError,
)
from .webif.discovery import (
    FORM_PAGES,
    HEATING_PAGE,
    MENU_PATHS,
    PAGE_MENUS,
    find_pages,
    in_both_languages,
    moved,
    shared_addresses,
)
from .webif.setting import (
    POWER_LIMIT,
    NotReadBack,
    OtherValueShown,
    Setting,
    write_setting,
)
from .webif_polled import Page
from .webif_shared import leave_out
from .webif_write_messages import raise_translated
from .weishaupt_modbus_api.write_budget import WriteBudget

_LOGGER = logging.getLogger(__name__)

# User decision 2026-10-02: one failure is ridden out, the second in a row
# shows unavailable, the third stops polling.
FAILURES_KEPT = 1
FAILURES_TO_STOP = 3
# User decision 2026-10-17: a stop held until a reload by hand, and a page
# broken for minutes stopped polling for days. The page is asked alone again
# after the first wait, each failed try doubling it up to the longest.
FIRST_RETRY_SECONDS = 15 * 60.0
LONGEST_RETRY_SECONDS = 2 * 3600.0
# Home Assistant plans the next round from the whole second, so a round may
# start up to a second before a page's interval is over. Five cover that with
# room to spare and read a page at most 5 s early, nothing next to minutes.
DUE_SLACK_SECONDS = 5.0
# Home Assistant reads a wait of zero as "no polling", and a page can fall
# due just as a round asking another one ends: it gets the next round a
# second later instead.
SHORTEST_WAIT_SECONDS = 1.0
STOPPED_ISSUE = "webif_stopped"
# The same stop, by a page the search after new codes did not find: only a
# reconfigure, whose visit searches every page, brings it back for sure.
NOT_FOUND_AGAIN_ISSUE = "webif_stopped_not_found_again"
# Spec decision, 2026-10-09: a write starts after this long without a further
# request, so arrow clicks and second thoughts make one write, of the last.
DEBOUNCE_SECONDS = 5.0
Values = dict[str, str]


@dataclass
class _Reading:
    values: Values | None = None
    read_at: float | None = None
    asked_at: float | None = None
    failures: int = 0


def _oldest_first(reading: _Reading) -> float:
    return -math.inf if reading.read_at is None else reading.read_at


@dataclass(frozen=True)
class _Stopped:
    """The page that stopped polling, why, and when it is asked again."""

    page: Page
    reason: str
    wait: float
    retry_at: float


def _retry_due(stopped: _Stopped, now: float) -> list[Page]:
    """While stopped, only the stopping page, once its wait is over."""
    if stopped.retry_at - now > DUE_SLACK_SECONDS:
        return []
    return [stopped.page]


def _whole_or_moved(page: Page, text: str) -> bool:
    """Whole, or moved: asking again cannot mend either.

    Moved is the overview or another circuit's page in its place.
    """
    return page.is_whole(text) or moved(text, page.path) or page.under_another(text)


def _failure_reason(page: Page, error: WebifError) -> str:
    """The error's summary, with the titles a broken page lacked."""
    if not isinstance(error, Broken) or error.text is None:
        return error.summary
    return f"{error.summary} ({page.flaws(error.text)})"


class WriteFailed(WebifError):
    """A write ended in a fault of this code or of a library; it is logged."""


@dataclass
class _Burst:
    """Requests not written yet: the last target, and the outcome they share.

    It takes requests until its write starts, also while that write waits
    for one still running. Each setting has its own.
    """

    page: str
    setting: Setting
    target: int
    outcome: asyncio.Future[None]

    @property
    def name(self) -> str:
        """The setting as its user finds it in the controller's menus.

        Each language's path in full: "Wärmepumpe › Heizen ›
        Leistungsbegrenzung (heat pump › heating › output limit)".
        """
        german, english = MENU_PATHS[self.page]
        if self.setting.form_page:
            # The page is the setting's own: its path ends in its title.
            return in_both_languages(german, english)
        english_title = self.setting.english_title
        return in_both_languages(
            f"{german} › {self.setting.title}",
            None
            if english is None or english_title is None
            else f"{english} › {english_title}",
        )


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
        log_expected_outages_at_info(_LOGGER)
        self._shortest_interval = shortest
        self._client = client
        self._pages = polled
        self.budget = budget
        self._readings = {page.key: _Reading() for page in polled}
        self._clock = clock
        self._stopped: _Stopped | None = None
        self._refused: LoginRefused | None = None
        self._traceback_logged = False
        # A write's own: its message sends the user to the log, also after a
        # round's traceback was logged.
        self._write_traceback_logged = False
        # A round cut short by a timeout gives the controller a rest: no round
        # asks a page before it ends, not one a write planned, not one asked
        # for by hand. A write the user asks for still goes out.
        self._rest_until = -math.inf
        # Counted: two dialogs of one entry may visit at once.
        self._dialog_visits = 0
        self._no_dialog_visit = asyncio.Event()
        self._no_dialog_visit.set()
        # Held for a round's page and for a whole write: a round request
        # waiting on the client got in between a write's own requests.
        self._asking = asyncio.Lock()
        # By page and setting title.
        self._bursts: dict[tuple[str, str], _Burst] = {}
        self._quiets: dict[tuple[str, str], asyncio.TimerHandle] = {}
        self._issue = f"{STOPPED_ISSUE}_{config_entry.entry_id}"
        # The slowest answer since the last round that asked anything: a write
        # in between asks through the same client, and its answers count too.
        self.answer_seconds: float | None = None
        # How often the pages were searched again since the setup.
        self.searched_again = 0
        self._searched_in_round = False
        self._search_failed = False
        # The pages the last search did not find, kept at their old address.
        self._lost: set[str] = set()
        # Pages it could not tell apart were left out: until the reload
        # nothing is written.
        self._reloading = False
        # Reloading the entry resumes polling at once after a stop.
        ir.async_delete_issue(hass, CONST.DOMAIN, self._issue)

    @asynccontextmanager
    async def dialog_visiting(self) -> AsyncIterator[None]:
        """Hold the rounds from the next page on while a dialog of this entry visits.

        Both share the gap between requests: live, the rounds took 6 of the
        18 requests of a reconfigure, and the dialog waited 134 s instead of
        about 90. After a visit that works out the entry reloads anyway.
        """
        self._dialog_visits += 1
        self._no_dialog_visit.clear()
        try:
            yield
        finally:
            self._dialog_visits -= 1
            if not self._dialog_visits:
                self._no_dialog_visit.set()

    def polls(self, key: str) -> bool:
        """Whether the page of `key` is among the ones polled."""
        return any(page.key == key for page in self._pages)

    def _page(self, key: str) -> Page:
        return next(page for page in self._pages if page.key == key)

    async def async_shutdown(self) -> None:
        """Stop, and take a stop notice along: the entry it names is unloading.

        A write still waiting for its requests to settle is called off.
        """
        await super().async_shutdown()
        for quiet in self._quiets.values():
            quiet.cancel()
        self._quiets.clear()
        for burst in self._bursts.values():
            burst.outcome.set_exception(Closed("the entry unloads"))
        self._bursts.clear()
        ir.async_delete_issue(self.hass, CONST.DOMAIN, self._issue)

    async def set_power_limit(self, target: int) -> None:
        """Write the heating power limit once the requests have settled."""
        await self.set_setting(HEATING_PAGE, POWER_LIMIT, target)

    async def set_setting(self, page: str, setting: Setting, target: int) -> None:
        """Write `setting` on `page` as the option `target` once the requests settled.

        Requests less than DEBOUNCE_SECONDS apart make one write, of the last
        target, and each of them gets its outcome; each setting has its own
        bursts. Raises a translated HomeAssistantError when the write is
        refused or fails.
        """
        self._refuse_write()
        key = (page, setting.title)
        if key not in self._bursts:
            burst = _Burst(page, setting, target, self.hass.loop.create_future())
            burst.outcome.add_done_callback(_retrieved)
            self._bursts[key] = burst
        burst = self._bursts[key]
        burst.target = target
        if (quiet := self._quiets.get(key)) is not None:
            quiet.cancel()
        self._quiets[key] = self.hass.loop.call_later(
            DEBOUNCE_SECONDS, self._start_write, burst
        )
        try:
            await asyncio.shield(burst.outcome)
        except (WebifError, HomeAssistantError) as error:
            raise_translated(error, burst.name)

    def _refuse_write(self) -> None:
        """Refuse a write that cannot go out, before any request."""
        if self._reloading:
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN, translation_key="webif_write_reloading"
            )
        if self._stopped is not None:
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
        self._quiets.pop((burst.page, burst.setting.title), None)
        self.config_entry.async_create_background_task(
            self.hass, self._write(burst), f"weishaupt-webif {burst.setting.title}"
        )

    async def _write(self, burst: _Burst) -> None:
        """One write, its outcome handed to every caller that asked for it."""
        outcome = burst.outcome
        key = (burst.page, burst.setting.title)
        counted = False
        handed_on = False

        def count_write() -> None:
            nonlocal counted
            counted = True
            self.budget.record_write()

        try:
            async with self._asking:
                # Live a write takes 20 to 60 s: the requests made while
                # another ran joined this one, and from here on they wait
                # for a write of their own.
                if self._bursts.get(key) is burst and key in self._quiets:
                    # A change since this write was started restarted the
                    # quiet time; the write that quiet time starts takes it.
                    handed_on = True
                    return
                if self._bursts.get(key) is burst:
                    del self._bursts[key]
                if outcome.done():
                    # Written by a write started for it earlier, or called
                    # off by the unload while it waited for its turn.
                    return
                self._refuse_write()
                # Looked up under the lock: a round before it may have found
                # the page at a new address.
                page = self._page(burst.page)
                text = await write_setting(
                    self._client,
                    page.path,
                    page.is_whole,
                    page.owned,
                    burst.setting,
                    burst.target,
                    count_write,
                )
                self._publish(page, text)
        except (WebifError, HomeAssistantError) as error:
            self._after_failed_write(error, self._page(burst.page))
            outcome.set_exception(error)
        except Exception as error:
            # A fault of this code or of a library, not of the pump.
            if not self._write_traceback_logged:
                self._write_traceback_logged = True
                _LOGGER.exception(
                    "Writing %s failed on an unexpected error", burst.name
                )
            if counted:
                # The save may have gone out: the next round asks.
                self._readings[burst.page].asked_at = None
            outcome.set_exception(WriteFailed(type(error).__name__))
        else:
            outcome.set_result(None)
        finally:
            # Cancelled as the entry unloads: no caller may wait for good.
            if not outcome.done() and not handed_on:
                outcome.set_exception(_cut_off(counted=counted))

    def _after_failed_write(self, error: Exception, page: Page) -> None:
        if isinstance(error.__cause__, Unreachable):
            self._rest()
        if isinstance(error.__cause__, LoginRefused):
            # Raised as not read back, so that the user does not save again.
            self._refused = error.__cause__
        if isinstance(error, OtherValueShown):
            self._publish(page, error.text)
        elif isinstance(error, (MaybeSaved, NotReadBack)):
            # Unknown what the controller holds: the next round asks.
            self._readings[page.key].asked_at = None
        elif isinstance(error, LoginRefused):
            self._refused = error

    def _publish(self, page: Page, text: str) -> None:
        """A page a write read counts as that page's reading, as of now."""
        self._store(page, text)
        self._readings[page.key].asked_at = self._readings[page.key].read_at
        # Home Assistant restarts its refresh timer here, with the wait the
        # last round planned: the next page due would come that much late.
        self._plan_next_round()
        self.async_set_updated_data(self._published())

    @property
    def traffic(self) -> Traffic:
        """What the client has asked of the controller so far."""
        return self._client.traffic

    def diagnostics(self) -> dict[str, Any]:
        """The polling's own state, for the diagnostics download."""
        now = self._clock()
        stopped = self._stopped
        return {
            # Not `data`: Home Assistant keeps the last data a refresh returned,
            # through a stop the values from before it.
            "pages": self._published(),
            "answer_seconds": self.answer_seconds,
            "traffic": asdict(self.traffic),
            "stopped_by": None if stopped is None else self._stopped_by(stopped),
            "retry_minutes": None if stopped is None else stopped.wait / 60,
            "login_refused": self._refused is not None,
            "searched_again": self.searched_again,
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
        if not await self._round():
            self._rest()
        self._plan_next_round()
        if (slowest := self._client.take_slowest_answer()) is not None:
            self.answer_seconds = slowest
        if self._stopped is not None:
            page_name, error = self._stopped_by(self._stopped)
            # The exception's own text, of the same name as the issue's; a
            # literal, as the translation check reads it from the source.
            # At INFO: the stop's repair notice says what to do.
            raise ExpectedOutage(
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
        self._searched_in_round = False
        self._search_failed = False
        # By key: a search in the round gives the pages left new addresses.
        for key in [page.key for page in self._due()]:
            await self._no_dialog_visit.wait()
            async with self._asking:
                # A dialog may have begun while the round waited for a write;
                # the pages left stay due for the round after its visit.
                if not self._no_dialog_visit.is_set():
                    return True
                # The write the round waited for may have read the page, or
                # run into a timeout.
                page = next((page for page in self._due() if page.key == key), None)
                if page is None:
                    continue
                fetched = await self._fetch(page)
            if not fetched:
                return False
        return True

    def _rest(self) -> None:
        self._rest_until = self._clock() + self._shortest_interval.total_seconds()

    @staticmethod
    def _stopped_by(stopped: _Stopped) -> tuple[str, str]:
        """The stopping page as its user finds it in the menus, and why."""
        return PAGE_MENUS[stopped.page.key], stopped.reason

    def _plan_next_round(self) -> None:
        # Home Assistant plans the next round from the end of every refresh,
        # one requested by hand included: on a fixed interval, an update just
        # before a page was due put that page off by a whole round.
        now = self._clock()
        wait = max(
            self._next_due_in(now),
            self._rest_until - now,
            SHORTEST_WAIT_SECONDS,
        )
        self.update_interval = timedelta(seconds=wait)

    def _next_due_in(self, now: float) -> float:
        if self._stopped is not None:
            return self._stopped.retry_at - now
        return min(self._due_in(page, now) for page in self._pages)

    def _due(self) -> list[Page]:
        now = self._clock()
        if self._rest_until - now > DUE_SLACK_SECONDS:
            return []
        if self._stopped is not None:
            return _retry_due(self._stopped, now)
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
            page, text = await self._read(page)
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
            # A search before the failure gave the page a new address.
            page = self._page(page.key)
            _LOGGER.debug("%s: %s", page.key, error)
            if not self._failed(page, _failure_reason(page, error)):
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
            return self._failed(self._page(page.key), type(error).__name__)
        self._store(page, text)
        if self._stopped is not None:
            # While stopped, the rounds ask the stopping page alone.
            self._stopped = None
            ir.async_delete_issue(self.hass, CONST.DOMAIN, self._issue)
        return True

    async def _read(self, page: Page) -> tuple[Page, str]:
        """The page whole, and as it is polled now: moved, at its new address.

        The overview or another circuit's page in its place is not asked for
        twice, and leads to one search a round. One at a new address too is a
        failure of the page, as is a page the search did not find again, under
        its own name.
        """
        text = await self._client.page(page.path, partial(_whole_or_moved, page))
        if page.is_whole(text):
            # Under its own menu again: a later failure is no longer a loss.
            self._lost.discard(page.key)
            return page, text
        if not self._searched_in_round:
            self._searched_in_round = True
            try:
                await self._search_again(text)
            except WebifError:
                self._search_failed = True
                raise
            page = self._page(page.key)
            if page.key not in self._lost:
                text = await self._client.page(
                    page.path, partial(_whole_or_moved, page)
                )
                if page.is_whole(text):
                    return page, text
        if page.key in self._lost:
            raise Broken(f"{page.key}: not found again", "not found again")
        if self._search_failed:
            # No page got a new address: it failed as the search did.
            raise Broken(f"{page.key}: search failed", "search failed")
        raise Broken(f"{page.key}: moved again", "moved again", text=text)

    async def _search_again(self, overview: str) -> None:
        """Each page's new address, from the main menus `overview` shows.

        `overview` is the overview in a page's place or another circuit's page
        at its address; both list the main menus under their current codes.

        Only the pages the entry has get one: a page found only now joins
        after a reconfigure, whose visit checks it. A page not found keeps
        its old address, and the ones found are polled at theirs; should a
        page found have that address, the lost page is left out and the entry
        reloads.
        """
        found = await find_pages(self._client, overview, reads=self._polled_keys)
        data = self.config_entry.data
        polled = {page.key for page in self._pages}
        # A form found at setup but switched off is neither read nor written:
        # its old address goes quietly, and switching it on asks for a
        # reconfigure, whose visit checks it.
        unread = {key for key in FORM_PAGES if key not in found.keys() | polled}
        pages = {
            key: found.get(key, path)
            for key, path in data[CONF.PAGES].items()
            if key not in unread
        }
        gone = {key for key in shared_addresses(pages) if key not in found}
        # A form's generic fields cannot tell which setting owns an address
        # given to another: at its old address another setting's form would
        # pass for it.
        gone |= {key for key in FORM_PAGES if key in polled and key not in found}
        if gone:
            # Kept at its old address, a page not found again would read the
            # page found there as its own, and a write to it would land there.
            self._reloading = True
            leave_out(self.hass, self.config_entry, gone, pages)
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
            raise Closed("pages left out; the entry reloads")
        self._lost = {page.key for page in self._pages if page.key not in found}
        self._pages = [
            replace(page, path=found.get(page.key, page.path)) for page in self._pages
        ]
        self.searched_again += 1
        # By key, without the addresses, which carry the controller's codes.
        _LOGGER.info(
            "The pages moved to new addresses and were found again%s",
            f", but not {', '.join(sorted(self._lost))}" if self._lost else "",
        )
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**data, CONF.PAGES: pages}
        )

    async def _polled_keys(self, _: dict[str, str]) -> set[str]:
        """The pages polled: a settings menu is opened only for one of them."""
        return {page.key for page in self._pages}

    def _store(self, page: Page, text: str) -> None:
        reading = self._readings[page.key]
        # Only what a sensor reads: the rest of a page would go into the
        # diagnostics download, an identifying entry among it maybe.
        read = page.required | page.optional
        reading.values = {
            title: shown for title, shown in page.read(text) if title in read
        }
        reading.read_at = self._clock()
        if reading.failures > FAILURES_KEPT:
            _LOGGER.info("%s read again", page.key)
        reading.failures = 0

    def _failed(self, page: Page, reason: str) -> bool:
        """Count a failure of the page; False when it stopped the polling."""
        reading = self._readings[page.key]
        reading.failures += 1
        _LOGGER.debug("%s failed, %d in a row: %s", page.key, reading.failures, reason)
        if reading.failures == FAILURES_KEPT + 1:
            _LOGGER.info("%s unavailable: %s", page.key, reason)
        if reading.failures < FAILURES_TO_STOP:
            return True
        self._stop(page, reason)
        return False

    def _stop(self, page: Page, reason: str) -> None:
        """Stop polling, or wait longer for a page that failed its retry."""
        if self._stopped is None:
            # The stopping round rests the shortest interval anyway.
            wait = max(FIRST_RETRY_SECONDS, self._shortest_interval.total_seconds())
            # Hours may pass until polling resumes: the other pages' values
            # from before the stop showed again as current. Each page is asked
            # once polling resumes, not after its interval, an hour maybe.
            for reading in self._readings.values():
                reading.values = None
                reading.asked_at = None
        else:
            wait = min(2 * self._stopped.wait, LONGEST_RETRY_SECONDS)
        self._stopped = _Stopped(page, reason, wait, self._clock() + wait)
        ir.async_create_issue(
            self.hass,
            CONST.DOMAIN,
            self._issue,
            is_fixable=False,
            # It goes by itself once the page comes whole again.
            severity=ir.IssueSeverity.WARNING,
            translation_key=NOT_FOUND_AGAIN_ISSUE
            if page.key in self._lost
            else STOPPED_ISSUE,
            translation_placeholders={
                "page": PAGE_MENUS[page.key],
                "error": reason,
            },
        )
