"""How the web interface's pages are kept fresh, and when polling stops.

Driven with a real Home Assistant core and a fake client that answers each
page from a script; the client's own rules are tested in test_webif_client.
"""

import asyncio
from dataclasses import replace
from datetime import timedelta
import json
import logging
import pathlib
import re

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.weishaupt_modbus import webif_coordinator
from custom_components.weishaupt_modbus.const import CONST
from custom_components.weishaupt_modbus.webif.client import (
    SAVE,
    Broken,
    Closed,
    LoginRefused,
    MaybeSaved,
    NotSaved,
    Traffic,
    Unreachable,
)
from custom_components.weishaupt_modbus.webif.discovery import (
    HEAT_PUMP_PAGE,
    HEATING_PAGE,
    STATISTICS_PAGE,
)
from custom_components.weishaupt_modbus.webif_coordinator import (
    STOPPED_ISSUE,
    Page,
    WebifCoordinator,
    polled_pages,
)
from custom_components.weishaupt_modbus.weishaupt_modbus_api.write_budget import (
    WriteBudget,
)
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .locking import until
from .webif_stand_in import heating_page, limit_leaf, limit_segment

STACK = "/settings_export.html?stack="
PUMP_MENU = "64000001000000000080000A0B010002000301"
HEATING_MENU = "64001800000000000080000A0B020003000401"
LIMIT = "64001807000000003C40000A0B030011010401"
QUARTER_HOUR = timedelta(minutes=15)
HOUR = timedelta(hours=1)
HEAT_PUMP = Page(
    "heat_pump",
    STACK + "0C000C22000000000000000A0B020003000401",
    frozenset({"Hochdruck"}),
    QUARTER_HOUR,
)
STATISTICS = Page(
    "statistics",
    STACK + "0C000C23000000000000000A0B020003000401",
    frozenset({"JAZ Jahr"}),
    HOUR,
)
HEATING = Page(
    "heating",
    STACK + f"{PUMP_MENU},{HEATING_MENU}",
    frozenset({"Leistungsbegrenzung"}),
    HOUR,
    menu=True,
)
TRANSLATIONS = pathlib.Path(webif_coordinator.__file__).parent


def value(title, text):
    return f'<div class="nav-link browseobj" role="tab"><h5>{title}</h5>{text}</div>'


def link(path, title, shown):
    return f'<a class="nav-link browseobj" href="{path}"><h5>{title}</h5>{shown}</a>'


WHOLE = {
    HEAT_PUMP.path: value("Hochdruck", "24.4 BAR") + value("Verdichter", "2568 rpm"),
    STATISTICS.path: value("JAZ Jahr", "4.16"),
    HEATING.path: link(f"{HEATING.path},{LIMIT}", "Leistungsbegrenzung", "60 %"),
}
BLANK_VALUE = value("Hochdruck", "")


class FakeClient:
    """Answers each page from a script; the last answer repeats.

    An answer is a page's text or an error to raise. Like the real client,
    it returns only a text `complete` accepts. `slowest` is the answer time
    the next round takes from it.
    """

    def __init__(self):
        self.asked = []
        self.script = {}
        self.traffic = Traffic()
        self.slowest = None
        self.saved = []
        self.save_error = None
        self.save_gate = None

    def shows_power_limit(self, limit):
        """The heating page and the limit's own page, as the controller
        serves them for `limit`."""
        self.script[HEATING.path] = [heating_page(limit)]
        self.script[f"{HEATING.path},{limit_segment(limit)}"] = [limit_leaf(limit)]

    async def save(self, form, value):
        self.asked.append(SAVE)
        self.saved.append(value)
        if self.save_gate is not None:
            await self.save_gate.wait()
        if self.save_error is not None:
            raise self.save_error
        self.shows_power_limit(value)

    def answer(self, page, *answers):
        self.script[page.path] = list(answers)

    def take_slowest_answer(self):
        slowest, self.slowest = self.slowest, None
        return slowest

    async def page(self, path, complete):
        self.asked.append(path)
        answers = self.script[path] if path in self.script else [WHOLE[path]]
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, BaseException):
            raise answer
        if not complete(answer):
            raise Broken(path)
        return answer


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(domain=CONST.DOMAIN, data={})
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def coordinator(hass, entry, client, clock):
    return WebifCoordinator(
        hass,
        entry,
        client,
        [HEAT_PUMP, STATISTICS, HEATING],
        budget=WriteBudget(warn_at=0, limit=10),
        clock=clock,
    )


@pytest.fixture
def listened(coordinator):
    """Home Assistant plans rounds only for a coordinator something listens to."""
    stop_listening = coordinator.async_add_listener(lambda: None)
    yield coordinator
    stop_listening()


async def round_at(coordinator, clock, seconds):
    clock.now = seconds
    await coordinator.async_refresh()


def intervals(options):
    """Each page's interval, as the entry's options set it."""
    paths = {key: f"/{key}" for key in (HEAT_PUMP_PAGE, STATISTICS_PAGE, HEATING_PAGE)}
    required = dict.fromkeys(paths, frozenset())
    return {page.key: page.interval for page in polled_pages(paths, options, required)}


def test_without_options_the_pages_keep_the_default_intervals():
    """User decision, 2026-10-03: 5 minutes for the heat pump page, 15 for
    the statistics and the heating settings."""
    assert intervals({}) == {
        HEAT_PUMP_PAGE: timedelta(minutes=5),
        STATISTICS_PAGE: QUARTER_HOUR,
        HEATING_PAGE: QUARTER_HOUR,
    }


def test_each_page_follows_its_own_interval_option():
    """User wish, 2026-10-03: the statistics and the heating settings on an
    interval of their own, like the heat pump page."""
    options = {
        CONST.OPTION_WEBIF_HEAT_PUMP_INTERVAL: 2,
        CONST.OPTION_WEBIF_STATISTICS_INTERVAL: 10,
        CONST.OPTION_WEBIF_HEATING_INTERVAL: 30,
    }

    assert intervals(options) == {
        HEAT_PUMP_PAGE: timedelta(minutes=2),
        STATISTICS_PAGE: timedelta(minutes=10),
        HEATING_PAGE: timedelta(minutes=30),
    }


async def test_the_first_round_reads_every_page(coordinator, client, clock):
    await round_at(coordinator, clock, 0)

    assert client.asked == [HEAT_PUMP.path, STATISTICS.path, HEATING.path]
    assert coordinator.data == {
        "heat_pump": {"Hochdruck": "24.4 BAR"},
        "statistics": {"JAZ Jahr": "4.16"},
        "heating": {"Leistungsbegrenzung": "60 %"},
    }


async def test_a_page_keeps_only_the_titles_its_sensors_read(
    coordinator, client, clock
):
    """Every entry of a page went into the diagnostics download, which is
    meant for public issues, and a page may well carry an identifying one."""
    client.answer(
        HEAT_PUMP, WHOLE[HEAT_PUMP.path] + value("Seriennummer", "SN-0000-0000")
    )

    await round_at(coordinator, clock, 0)

    assert coordinator.data["heat_pump"] == {"Hochdruck": "24.4 BAR"}


async def test_a_page_of_7_minutes_beside_one_of_5_comes_after_7_not_10(
    hass, entry, client, clock
):
    """The round came as often as the most frequent page, so a slower page
    waited past its own interval for the faster page's next round. Each
    round now comes when the next page is due, and none asks nothing."""
    polled = [
        replace(HEAT_PUMP, interval=timedelta(minutes=5)),
        replace(STATISTICS, interval=timedelta(minutes=7)),
    ]
    coordinator = WebifCoordinator(
        hass, entry, client, polled, budget=WriteBudget(0, 10), clock=clock
    )
    rounds = []
    while clock.now <= 15 * 60:
        client.asked.clear()
        await coordinator.async_refresh()
        rounds.append((clock.now / 60, list(client.asked)))
        clock.now += coordinator.update_interval.total_seconds()

    assert rounds == [
        (0, [HEAT_PUMP.path, STATISTICS.path]),
        (5, [HEAT_PUMP.path]),
        (7, [STATISTICS.path]),
        (10, [HEAT_PUMP.path]),
        (14, [STATISTICS.path]),
        (15, [HEAT_PUMP.path]),
    ]


async def test_a_round_ended_by_a_timeout_gives_the_controller_a_rest(
    coordinator, client, clock
):
    """The pages a timeout kept the round from are due at once; asking them
    right away would leave a struggling controller no rest."""
    client.answer(HEAT_PUMP, Unreachable("timeout"))

    await round_at(coordinator, clock, 0)

    assert client.asked == [HEAT_PUMP.path]
    assert coordinator.update_interval == QUARTER_HOUR


async def test_a_page_is_asked_for_again_once_its_interval_is_over(
    coordinator, client, clock
):
    await round_at(coordinator, clock, 0)
    client.asked.clear()

    await round_at(coordinator, clock, 15 * 60)
    assert client.asked == [HEAT_PUMP.path]

    client.asked.clear()
    await round_at(coordinator, clock, 15 * 60 + 60)
    assert client.asked == []


async def test_a_refresh_by_hand_does_not_put_the_next_round_off(
    hass, listened, client, clock
):
    """Home Assistant plans the next round from the end of every refresh, one
    requested by hand included: an update shortly before a page was due put
    that page off by a whole round."""
    await round_at(listened, clock, 0)
    await round_at(listened, clock, 15 * 60 - 10)
    client.asked.clear()
    clock.now = 15 * 60

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert client.asked == [HEAT_PUMP.path]


class SlowClient(FakeClient):
    """Each answer takes `seconds` on the clock, as the controller's do."""

    def __init__(self, clock, seconds):
        super().__init__()
        self.clock = clock
        self.seconds = seconds

    async def page(self, path, complete):
        self.clock.now += self.seconds
        return await super().page(path, complete)


async def test_a_page_falling_due_as_the_round_ends_still_gets_a_round(
    hass, entry, clock
):
    """A wait of exactly zero reads as "no polling" to Home Assistant: a page
    that fell due just as a round reading another page ended stopped the
    polling for good, with no stop and no notice."""
    client = SlowClient(clock, seconds=6)
    polled = [HEAT_PUMP, replace(STATISTICS, interval=QUARTER_HOUR)]
    coordinator = WebifCoordinator(
        hass, entry, client, polled, budget=WriteBudget(0, 10), clock=clock
    )
    stop_listening = coordinator.async_add_listener(lambda: None)
    await round_at(coordinator, clock, 0)
    await round_at(coordinator, clock, 15 * 60)
    client.asked.clear()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert client.asked == [STATISTICS.path]
    stop_listening()


async def test_a_round_a_second_early_still_reads_a_due_page(
    coordinator, client, clock
):
    """Home Assistant plans the next round from the whole second."""
    await round_at(coordinator, clock, 0)
    client.asked.clear()

    await round_at(coordinator, clock, 15 * 60 - 1)

    assert client.asked == [HEAT_PUMP.path]


async def test_the_page_with_the_oldest_reading_goes_first(coordinator, client, clock):
    for quarter in range(4):
        await round_at(coordinator, clock, quarter * 15 * 60)
    client.asked.clear()

    await round_at(coordinator, clock, 60 * 60)

    assert client.asked == [STATISTICS.path, HEATING.path, HEAT_PUMP.path]


async def test_after_a_timeout_the_round_asks_nothing_more(coordinator, client, clock):
    client.answer(HEAT_PUMP, Unreachable("timeout"))

    await round_at(coordinator, clock, 0)

    assert client.asked == [HEAT_PUMP.path]


async def test_a_broken_page_does_not_end_the_round(coordinator, client, clock):
    """The server answered; only that page's content was wrong."""
    client.answer(HEAT_PUMP, BLANK_VALUE)

    await round_at(coordinator, clock, 0)

    assert client.asked == [HEAT_PUMP.path, STATISTICS.path, HEATING.path]


async def test_one_failure_keeps_the_values_and_the_second_takes_them_away(
    coordinator, client, clock
):
    await round_at(coordinator, clock, 0)
    kept = coordinator.data["heat_pump"]
    client.answer(HEAT_PUMP, BLANK_VALUE, BLANK_VALUE, WHOLE[HEAT_PUMP.path])

    await round_at(coordinator, clock, 15 * 60)
    assert coordinator.data["heat_pump"] == kept

    await round_at(coordinator, clock, 30 * 60)
    assert coordinator.data["heat_pump"] is None
    assert coordinator.data["statistics"] is not None

    await round_at(coordinator, clock, 45 * 60)
    assert coordinator.data["heat_pump"] == kept


async def test_a_page_never_read_has_no_values_to_keep(coordinator, client, clock):
    client.answer(HEAT_PUMP, BLANK_VALUE)

    await round_at(coordinator, clock, 0)

    assert coordinator.data["heat_pump"] is None


async def test_the_third_failure_in_a_row_stops_polling(
    hass, coordinator, client, clock, entry
):
    """The notice names the page as its user finds it in the menus; it said
    "heat_pump", the integration's own key."""
    client.answer(HEAT_PUMP, Unreachable("timeout"))
    for quarter in range(3):
        await round_at(coordinator, clock, quarter * 15 * 60)

    assert not coordinator.last_update_success
    assert coordinator.update_interval is None
    issue = ir.async_get(hass).async_get_issue(
        CONST.DOMAIN, f"{STOPPED_ISSUE}_{entry.entry_id}"
    )
    assert issue is not None
    assert issue.translation_placeholders == {
        "page": "Info › Wärmepumpe",
        "error": "timeout",
    }

    client.asked.clear()
    await round_at(coordinator, clock, 24 * 60 * 60)
    assert client.asked == [], "no request once stopped, not even by hand"
    assert not coordinator.last_update_success


async def test_a_page_broken_for_good_stops_polling_while_others_still_come(
    coordinator, client, clock
):
    """Design: the brake also holds for a page that keeps arriving incomplete."""
    client.answer(STATISTICS, value("JAZ gesamt", "4.16"))
    for hour in range(3):
        await round_at(coordinator, clock, hour * 60 * 60)

    assert coordinator.update_interval is None


@pytest.mark.parametrize(
    "failure",
    [value("JAZ gesamt", "4.16"), RuntimeError("a fault of the parser")],
    ids=["broken", "unexpected"],
)
async def test_the_round_that_stops_polling_asks_nothing_more(
    coordinator, client, clock, failure
):
    """The brake spares the controller at once, not after the round."""
    client.answer(STATISTICS, failure)
    for hour in range(2):
        await round_at(coordinator, clock, hour * 60 * 60)
    client.asked.clear()

    await round_at(coordinator, clock, 2 * 60 * 60)

    assert client.asked == [STATISTICS.path]


@pytest.mark.parametrize("by_hand", [False, True], ids=["scheduled", "by_hand"])
async def test_a_failed_page_waits_its_own_interval_before_the_next_try(
    coordinator, client, clock, by_hand
):
    """A failed page was due again at every refresh: on the rounds of the
    most frequent page and on any update requested by hand, so its three
    failures came within minutes instead of three of its own intervals."""
    client.answer(STATISTICS, value("JAZ gesamt", "4.16"))
    for quarter in range(8):
        await round_at(coordinator, clock, quarter * 15 * 60)
        if by_hand:
            await coordinator.async_refresh()

    assert client.asked.count(STATISTICS.path) == 2
    assert coordinator.update_interval == QUARTER_HOUR


async def test_a_good_reading_in_between_starts_the_count_anew(
    coordinator, client, clock
):
    failed = Unreachable("timeout")
    client.answer(HEAT_PUMP, failed, failed, WHOLE[HEAT_PUMP.path], failed, failed)
    for quarter in range(5):
        await round_at(coordinator, clock, quarter * 15 * 60)

    assert coordinator.update_interval == QUARTER_HOUR


async def test_a_closed_client_ends_the_round_without_a_failure(
    hass, coordinator, client, clock, entry
):
    """An unload during a round closed the client; its pages counted as
    failures, and the third raised the stop notice for an entry that was
    going."""
    timeout = Unreachable("timeout")
    client.answer(STATISTICS, timeout, timeout, Closed("the client is closed"))
    for hour in range(3):
        await round_at(coordinator, clock, hour * 60 * 60)

    assert coordinator.update_interval is not None
    issue = ir.async_get(hass).async_get_issue(
        CONST.DOMAIN, f"{STOPPED_ISSUE}_{entry.entry_id}"
    )
    assert issue is None


async def test_an_unexpected_error_counts_toward_the_brake(coordinator, client, clock):
    """An error that is no web interface's, a fault of the parser say, passed
    the count: no brake, and Home Assistant took every sensor away, round
    after round, for good."""
    client.answer(HEAT_PUMP, RuntimeError("a fault of the parser"))
    for quarter in range(3):
        await round_at(coordinator, clock, quarter * 15 * 60)

    assert coordinator.update_interval is None
    assert coordinator.diagnostics()["page_states"]["heat_pump"]["failures"] == 3


async def test_an_unexpected_error_does_not_end_the_round(coordinator, client, clock):
    """The pump is not to blame, and the other pages' values stay of use."""
    client.answer(HEAT_PUMP, RuntimeError("a fault of the parser"))

    await round_at(coordinator, clock, 0)

    assert client.asked == [HEAT_PUMP.path, STATISTICS.path, HEATING.path]
    assert coordinator.last_update_success
    assert coordinator.data["statistics"] == {"JAZ Jahr": "4.16"}


# A round waiting on a hold that is never let go would otherwise hang until
# pytest's own limit cuts the whole run off.
HOLD_LIMIT_SECONDS = 5


async def settle():
    """Let every task run until it waits for something outside it."""
    for _ in range(10):
        await asyncio.sleep(0)


async def test_a_round_waits_while_a_dialog_of_its_entry_visits(coordinator, client):
    """Live, 2026-10-04: the entry's rounds took 6 of the 18 requests of a
    reconfigure, each behind the gap between requests both share, and the
    dialog waited 134 s. After a visit that works out the entry reloads."""
    async with coordinator.dialog_visiting():
        polling = asyncio.create_task(coordinator._async_update_data())
        await settle()
        assert client.asked == []

    async with asyncio.timeout(HOLD_LIMIT_SECONDS):
        await polling
    assert client.asked == [HEAT_PUMP.path, STATISTICS.path, HEATING.path]


@pytest.mark.parametrize("ending", ["cancelled", "failed"])
async def test_a_dialog_that_ends_early_lets_the_rounds_go(coordinator, client, ending):
    """A dialog closed mid-visit cancels the visit, and a visit can fail:
    either way the rounds must not wait on it for good, the sensors silently
    keeping their last values until a reload."""
    entered = asyncio.Event()

    async def visit():
        async with coordinator.dialog_visiting():
            entered.set()
            if ending == "failed":
                raise Unreachable("GET /index.html: TimeoutError")
            await asyncio.Event().wait()

    dialog = asyncio.create_task(visit())
    await entered.wait()
    dialog.cancel()
    with pytest.raises((asyncio.CancelledError, Unreachable)):
        await dialog

    async with asyncio.timeout(HOLD_LIMIT_SECONDS):
        await coordinator._async_update_data()
    assert client.asked == [HEAT_PUMP.path, STATISTICS.path, HEATING.path]


async def test_a_running_round_waits_between_its_pages(coordinator, client):
    """A dialog that starts while a round runs gets the gap from the next page
    on, not after the whole round."""
    visit = coordinator.dialog_visiting()
    ask = client.page

    async def ask_then_visit(path, complete):
        text = await ask(path, complete)
        if path == HEAT_PUMP.path:
            await visit.__aenter__()
        return text

    client.page = ask_then_visit
    polling = asyncio.create_task(coordinator._async_update_data())
    await settle()
    assert client.asked == [HEAT_PUMP.path]

    await visit.__aexit__(None, None, None)
    async with asyncio.timeout(HOLD_LIMIT_SECONDS):
        await polling
    assert client.asked == [HEAT_PUMP.path, STATISTICS.path, HEATING.path]


async def test_a_cancelled_page_goes_through_uncounted(coordinator, client, clock):
    """The entry unloading or Home Assistant stopping cancels a round. The
    catch for faults of this code must not take that for a failed page."""
    client.answer(HEAT_PUMP, asyncio.CancelledError())

    # Asked of the update itself: Home Assistant's refresh passes a
    # cancellation on only while its own task is being cancelled.
    with pytest.raises(asyncio.CancelledError):
        await coordinator._async_update_data()

    assert client.asked == [HEAT_PUMP.path]
    assert coordinator.diagnostics()["page_states"]["heat_pump"]["failures"] == 0


async def test_an_unexpected_error_logs_its_traceback_once(
    coordinator, client, clock, caplog
):
    """Home Assistant logged one every round, as an error."""
    client.answer(HEAT_PUMP, RuntimeError("a fault of the parser"))
    for quarter in range(3):
        await round_at(coordinator, clock, quarter * 15 * 60)

    tracebacks = [
        record
        for record in caplog.records
        if record.exc_info and record.levelno >= logging.ERROR
    ]
    assert len(tracebacks) == 1


async def test_the_stop_names_an_unexpected_error_by_its_kind_only(
    hass, coordinator, client, clock, entry
):
    """Its text may name the pump's address, which the notice and the
    download, meant for public issues, leave out."""
    client.answer(HEAT_PUMP, RuntimeError("http://192.0.2.10/index.html"))
    for quarter in range(3):
        await round_at(coordinator, clock, quarter * 15 * 60)

    issue = ir.async_get(hass).async_get_issue(
        CONST.DOMAIN, f"{STOPPED_ISSUE}_{entry.entry_id}"
    )
    assert issue.translation_placeholders["error"] == "RuntimeError"
    assert coordinator.diagnostics()["stopped_by"] == (
        "Info › Wärmepumpe",
        "RuntimeError",
    )


async def test_shutting_down_takes_the_stop_notice_along(
    hass, coordinator, client, clock, entry
):
    """The notice asks to reload an entry that is unloading or being deleted."""
    client.answer(HEAT_PUMP, Unreachable("timeout"))
    for quarter in range(3):
        await round_at(coordinator, clock, quarter * 15 * 60)

    await coordinator.async_shutdown()

    issue = ir.async_get(hass).async_get_issue(
        CONST.DOMAIN, f"{STOPPED_ISSUE}_{entry.entry_id}"
    )
    assert issue is None


async def test_a_refused_login_stops_at_once_and_asks_for_credentials(
    coordinator, client
):
    client.answer(HEAT_PUMP, LoginRefused("HTTP 303 to /index.html#wrongpassword"))

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
    assert client.asked == [HEAT_PUMP.path]


async def test_a_refused_login_is_not_sent_again_by_a_later_refresh(
    coordinator, client
):
    """Only the scheduled rounds stopped: every update requested by hand sent
    the refused login again while the reauth waited."""
    client.answer(HEAT_PUMP, LoginRefused("HTTP 303 to /index.html#wrongpassword"))
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
    client.asked.clear()

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
    assert client.asked == []


async def test_the_diagnostics_name_the_page_the_brake_stopped_on(
    coordinator, client, clock
):
    """Support could not tell the brake, a refused login and an outage apart;
    the download says what stopped the polling, and where."""
    client.answer(HEAT_PUMP, Unreachable("timeout"))
    for quarter in range(3):
        await round_at(coordinator, clock, quarter * 15 * 60)

    diagnostics = coordinator.diagnostics()

    assert diagnostics["stopped_by"] == ("Info › Wärmepumpe", "timeout")
    assert diagnostics["login_refused"] is False


async def test_the_diagnostics_tell_a_refused_login(coordinator, client):
    client.answer(HEAT_PUMP, LoginRefused("HTTP 303 to /index.html#wrongpassword"))
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

    diagnostics = coordinator.diagnostics()

    assert diagnostics["login_refused"] is True
    assert diagnostics["stopped_by"] is None


async def test_the_answer_time_is_the_slowest_of_the_last_round_that_asked(
    coordinator, client, clock
):
    """A round with nothing due asks nothing, and keeps the time shown, in
    the diagnostics download too."""
    client.slowest = 1.9
    await round_at(coordinator, clock, 0)
    assert coordinator.answer_seconds == 1.9

    await round_at(coordinator, clock, 60)

    assert coordinator.answer_seconds == 1.9
    assert coordinator.diagnostics()["answer_seconds"] == 1.9


async def test_the_diagnostics_tell_how_old_each_reading_is(coordinator, client, clock):
    """A support case tells a page never read from one read a while ago."""
    client.answer(STATISTICS, Unreachable("timeout"))
    await round_at(coordinator, clock, 0)
    clock.now = 120

    states = coordinator.diagnostics()["page_states"]

    assert states["heat_pump"] == {"failures": 0, "seconds_since_read": 120}
    assert states["statistics"] == {"failures": 1, "seconds_since_read": None}


async def test_a_reload_takes_the_stop_back(hass, entry, client, clock, coordinator):
    client.answer(HEAT_PUMP, Unreachable("timeout"))
    for quarter in range(3):
        await round_at(coordinator, clock, quarter * 15 * 60)

    WebifCoordinator(
        hass, entry, client, [HEAT_PUMP], budget=WriteBudget(0, 10), clock=clock
    )

    issues = ir.async_get(hass)
    assert (
        issues.async_get_issue(CONST.DOMAIN, f"{STOPPED_ISSUE}_{entry.entry_id}")
        is None
    )


@pytest.mark.parametrize(
    "name",
    [
        "strings.json",
        "translations/en.json",
        "translations/de.json",
        "translations/nl.json",
    ],
)
def test_the_stop_notice_names_the_page_and_the_error(name):
    texts = json.loads((TRANSLATIONS / name).read_text(encoding="utf-8"))
    notice = texts["issues"][STOPPED_ISSUE]

    for text in (notice["title"], notice["description"]):
        assert set(re.findall(r"\{(\w+)\}", text)) <= {"page", "error"}
    assert set(re.findall(r"\{(\w+)\}", notice["description"])) == {"page", "error"}


# Short enough for a test, long enough that the calls a test spaces out
# shorter than it still fall inside it on a busy machine.
QUIET = 0.2
INSIDE_QUIET = 0.12


@pytest.fixture
def quick(monkeypatch):
    """The quiet time a write waits for, shortened."""
    monkeypatch.setattr(webif_coordinator, "DEBOUNCE_SECONDS", QUIET)


def leaf(limit):
    return f"{HEATING.path},{limit_segment(limit)}"


async def first_round_showing(coordinator, client, clock, limit=60):
    client.shows_power_limit(limit)
    await round_at(coordinator, clock, 0)
    client.asked.clear()


async def refused(call):
    """The translation key and placeholders the call was refused with."""
    with pytest.raises(HomeAssistantError) as raised:
        await call
    return raised.value.translation_key, raised.value.translation_placeholders


async def test_a_burst_of_changes_writes_the_last_value_once(
    coordinator, client, clock, quick
):
    """Spec decision, 2026-10-09: each request restarts the quiet time, so
    arrow clicks a little apart are one write, of the last value."""
    await first_round_showing(coordinator, client, clock)

    calls = [asyncio.create_task(coordinator.set_power_limit(61))]
    for target in (65, 70):
        await asyncio.sleep(INSIDE_QUIET)
        calls.append(asyncio.create_task(coordinator.set_power_limit(target)))
    await asyncio.gather(*calls)

    assert client.saved == [70]
    assert coordinator.data["heating"] == {"Leistungsbegrenzung": "70 %"}


async def test_back_and_forth_to_the_shown_value_writes_nothing(
    coordinator, client, clock, quick
):
    """Owner decision, 2026-10-09: the controller is read fresh, and what it
    already shows is not written."""
    await first_round_showing(coordinator, client, clock)

    await asyncio.gather(
        coordinator.set_power_limit(61), coordinator.set_power_limit(60)
    )

    assert client.asked == [HEATING.path]
    assert client.saved == []
    assert coordinator.budget.writes_today == 0


async def test_a_change_during_a_write_is_written_after_it(
    coordinator, client, clock, quick
):
    await first_round_showing(coordinator, client, clock)
    client.save_gate = asyncio.Event()
    first = asyncio.create_task(coordinator.set_power_limit(61))
    await until(lambda: client.saved)

    second = asyncio.create_task(coordinator.set_power_limit(75))
    await asyncio.sleep(2 * QUIET)
    assert client.saved == [61], "two writes at once"
    client.save_gate.set()
    await asyncio.gather(first, second)

    assert client.saved == [61, 75]


async def test_changes_while_a_write_runs_are_one_write_after_it(
    coordinator, client, clock, quick
):
    """A write takes 20 to 60 s live. Each quiet time passing meanwhile made
    a write of its own, one EEPROM write more for every second thought."""
    await first_round_showing(coordinator, client, clock)
    client.save_gate = asyncio.Event()
    first = asyncio.create_task(coordinator.set_power_limit(61))
    await until(lambda: client.saved)

    second = asyncio.create_task(coordinator.set_power_limit(62))
    await asyncio.sleep(2 * QUIET)
    third = asyncio.create_task(coordinator.set_power_limit(63))
    await asyncio.sleep(2 * QUIET)
    client.save_gate.set()
    await asyncio.gather(first, second, third)

    assert client.saved == [61, 63]


async def test_an_unload_calls_off_a_write_waiting_for_its_turn(
    coordinator, client, clock, quick
):
    """Its quiet time over, a write waits for the one running; unloaded
    meanwhile, it must not ask anything once the running one ends."""
    await first_round_showing(coordinator, client, clock)
    client.save_gate = asyncio.Event()
    first = asyncio.create_task(coordinator.set_power_limit(61))
    await until(lambda: client.saved)
    second = asyncio.create_task(refused(coordinator.set_power_limit(62)))
    await asyncio.sleep(2 * QUIET)

    await coordinator.async_shutdown()
    client.save_gate.set()
    await first

    async with asyncio.timeout(HOLD_LIMIT_SECONDS):
        assert await second == ("webif_write_aborted", None)
    assert client.saved == [61]


async def test_every_call_of_a_burst_gets_its_outcome(
    coordinator, client, clock, quick
):
    await first_round_showing(coordinator, client, clock)
    client.save_error = MaybeSaved("POST /pro_save.html: TimeoutError")

    outcomes = await asyncio.gather(
        refused(coordinator.set_power_limit(61)),
        refused(coordinator.set_power_limit(62)),
    )

    assert outcomes == [("webif_write_maybe_saved", None)] * 2
    assert client.saved == [62]
    assert coordinator.budget.writes_today == 1


async def test_no_round_request_lands_between_the_write_requests(
    coordinator, client, clock, quick
):
    """A round's request waiting on the client got in between the write's
    read of the form, its save and its read-back."""
    await first_round_showing(coordinator, client, clock)
    client.save_gate = asyncio.Event()
    writing = asyncio.create_task(coordinator.set_power_limit(61))
    await until(lambda: client.saved)

    clock.now = 2 * 60 * 60
    polling = asyncio.create_task(coordinator._async_update_data())
    await settle()
    client.save_gate.set()
    await asyncio.gather(writing, polling)

    assert client.asked[:4] == [HEATING.path, leaf(60), SAVE, HEATING.path]
    assert HEAT_PUMP.path in client.asked[4:]


async def _stopped(coordinator, client, clock):
    client.answer(HEAT_PUMP, Unreachable("timeout"))
    for quarter in range(3):
        await round_at(coordinator, clock, quarter * 15 * 60)


async def _login_refused(coordinator, client, clock):
    client.answer(HEAT_PUMP, LoginRefused("HTTP 303 to /index.html#wrongpassword"))
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def _limit_used(coordinator, client, clock):
    coordinator.budget.limit = 1
    coordinator.budget.record_write()


@pytest.mark.parametrize(
    ("state", "outcome"),
    [
        (_stopped, ("webif_write_stopped", None)),
        (_login_refused, ("webif_write_login_refused", None)),
        (_limit_used, ("webif_write_limit_reached", {"limit": "1"})),
    ],
    ids=["polling stopped", "login refused", "daily limit used"],
)
async def test_a_write_is_refused_before_any_request(
    coordinator, client, clock, quick, state, outcome
):
    await state(coordinator, client, clock)
    client.asked.clear()

    assert await refused(coordinator.set_power_limit(61)) == outcome
    await asyncio.sleep(2 * QUIET)
    assert client.asked == []


async def test_a_write_is_refused_again_when_its_quiet_time_ends(
    coordinator, client, clock, quick
):
    """The limit was free when asked, and used up by the time the requests
    had settled."""
    await first_round_showing(coordinator, client, clock)
    coordinator.budget.limit = 1
    call = asyncio.create_task(refused(coordinator.set_power_limit(61)))
    await settle()

    coordinator.budget.record_write()

    assert await call == ("webif_write_limit_reached", {"limit": "1"})
    assert client.asked == []


async def test_a_write_publishes_the_heating_page_as_read(
    coordinator, client, clock, quick
):
    """The read-back is a reading: the next round does not ask for the
    heating page again before its interval from the write is over."""
    await first_round_showing(coordinator, client, clock)
    clock.now = 50 * 60

    await coordinator.set_power_limit(70)
    assert coordinator.data["heating"] == {"Leistungsbegrenzung": "70 %"}
    client.asked.clear()
    await round_at(coordinator, clock, 65 * 60)

    assert HEAT_PUMP.path in client.asked
    assert HEATING.path not in client.asked


async def test_another_value_shown_is_published_and_named(
    coordinator, client, clock, quick
):
    await first_round_showing(coordinator, client, clock)

    async def clamped(form, value):
        client.asked.append(SAVE)
        client.shows_power_limit(65)

    client.save = clamped

    assert await refused(coordinator.set_power_limit(61)) == (
        "webif_write_other_value",
        {"shown": "65", "target": "61"},
    )
    assert coordinator.data["heating"] == {"Leistungsbegrenzung": "65 %"}


def _unanswered(client):
    client.save_error = MaybeSaved("POST /pro_save.html: TimeoutError")


def _not_read_back(client):
    async def saved_then_broken(form, value):
        client.asked.append(SAVE)
        client.answer(HEATING, Broken(HEATING.path))

    client.save = saved_then_broken


@pytest.mark.parametrize(
    ("unclear", "key"),
    [
        (_unanswered, "webif_write_maybe_saved"),
        (_not_read_back, "webif_write_not_read_back"),
    ],
)
async def test_after_an_unclear_save_the_heating_page_is_due_at_once(
    coordinator, client, clock, quick, unclear, key
):
    await first_round_showing(coordinator, client, clock)
    unclear(client)

    assert (await refused(coordinator.set_power_limit(61)))[0] == key
    client.shows_power_limit(61)
    client.asked.clear()
    await round_at(coordinator, clock, 60)

    assert client.asked == [HEATING.path]


async def test_a_write_without_a_reading_still_reads_fresh(coordinator, client, quick):
    """No round has run yet, or the heating page failed twice: the write
    has no value to compare with and reads the controller."""
    client.shows_power_limit(60)

    await coordinator.set_power_limit(61)

    assert client.saved == [61]


async def test_an_unload_during_the_quiet_time_aborts_the_waiting_calls(
    coordinator, client, clock, quick
):
    await first_round_showing(coordinator, client, clock)
    call = asyncio.create_task(refused(coordinator.set_power_limit(61)))
    await settle()

    await coordinator.async_shutdown()

    async with asyncio.timeout(HOLD_LIMIT_SECONDS):
        assert await call == ("webif_write_aborted", None)
    await asyncio.sleep(2 * QUIET)
    assert client.asked == []


async def test_read_failures_of_a_write_do_not_trip_the_brake(
    coordinator, client, clock, quick
):
    """The brake stops the polling on three failed rounds; three writes that
    could not read their form are no round."""
    await first_round_showing(coordinator, client, clock)
    client.answer(HEATING, Broken(HEATING.path))

    for _ in range(3):
        assert await refused(coordinator.set_power_limit(61)) == (
            "webif_write_form_not_read",
            None,
        )

    states = coordinator.diagnostics()
    assert states["stopped_by"] is None
    assert states["page_states"]["heating"]["failures"] == 0


def _session_gone(client):
    client.save_error = NotSaved("POST /pro_save.html: HTTP 303 to /index.html")


def _other_form(client):
    client.script[leaf(60)] = [
        limit_leaf(60).replace('value="para_list"', 'value="para_text"')
    ]


def _login_refused_while_writing(client):
    client.answer(HEATING, LoginRefused("HTTP 303 to /index.html#wrongpassword"))


def _closed(client):
    client.answer(HEATING, Closed("the client is closed"))


@pytest.mark.parametrize(
    ("failure", "key"),
    [
        (_session_gone, "webif_write_not_saved"),
        (_other_form, "webif_write_form_mismatch"),
        (_login_refused_while_writing, "webif_write_login_refused"),
        (_closed, "webif_write_aborted"),
    ],
)
async def test_each_failure_of_a_write_has_its_message(
    coordinator, client, clock, quick, failure, key
):
    await first_round_showing(coordinator, client, clock)
    failure(client)

    assert await refused(coordinator.set_power_limit(61)) == (key, None)


async def test_a_login_refused_while_writing_stops_the_polling_too(
    coordinator, client, clock, quick
):
    """No wrong login is repeated, by a round or by the next write."""
    await first_round_showing(coordinator, client, clock)
    _login_refused_while_writing(client)
    await refused(coordinator.set_power_limit(61))
    client.asked.clear()

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
    assert client.asked == []


async def test_an_unexpected_error_of_a_write_logs_once(
    coordinator, client, clock, quick, caplog
):
    await first_round_showing(coordinator, client, clock)
    client.save_error = RuntimeError("a fault of the form")

    for _ in range(2):
        assert await refused(coordinator.set_power_limit(61)) == (
            "webif_write_failed",
            None,
        )

    tracebacks = [
        record
        for record in caplog.records
        if record.exc_info and record.levelno >= logging.ERROR
    ]
    assert len(tracebacks) == 1


async def test_a_write_cancelled_with_its_entry_lets_its_callers_go(
    coordinator, client, clock, entry, quick, monkeypatch
):
    """Home Assistant cancels an entry's background tasks when it goes: a
    caller must not wait for an outcome that will never be set."""
    await first_round_showing(coordinator, client, clock)
    started = []
    create = entry.async_create_background_task

    def noted(hass, target, name, eager_start=True):
        task = create(hass, target, name, eager_start)
        started.append(task)
        return task

    monkeypatch.setattr(entry, "async_create_background_task", noted)
    client.save_gate = asyncio.Event()
    call = asyncio.create_task(refused(coordinator.set_power_limit(61)))
    await until(lambda: client.saved)

    started[0].cancel()

    async with asyncio.timeout(HOLD_LIMIT_SECONDS):
        assert await call == ("webif_write_aborted", None)
