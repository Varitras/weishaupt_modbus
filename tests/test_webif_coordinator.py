"""How the web interface's pages are kept fresh, and when polling stops.

Driven with a real Home Assistant core and a fake client that answers each
page from a script; the client's own rules are tested in test_webif_client.
"""

from datetime import timedelta
import json
import pathlib
import re

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weishaupt_modbus import webif_coordinator
from custom_components.weishaupt_modbus.const import CONF, CONST
from custom_components.weishaupt_modbus.webif.client import (
    Broken,
    LoginRefused,
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
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir

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
EMPTY_COLUMN = value("Hochdruck", "")


class FakeClient:
    """Answers each page from a script; the last answer repeats.

    An answer is a page's text or an error to raise. Like the real client,
    it returns only a text `complete` accepts.
    """

    def __init__(self):
        self.asked = []
        self.script = {}

    def answer(self, page, *answers):
        self.script[page.path] = list(answers)

    async def page(self, path, complete):
        self.asked.append(path)
        answers = self.script.get(path, [WHOLE[path]])
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
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
        hass, entry, client, [HEAT_PUMP, STATISTICS, HEATING], clock=clock
    )


async def round_at(coordinator, clock, seconds):
    clock.now = seconds
    await coordinator.async_refresh()


def intervals(options):
    """Each page's interval, as the entry's options set it."""
    paths = {key: f"/{key}" for key in (HEAT_PUMP_PAGE, STATISTICS_PAGE, HEATING_PAGE)}
    entry = MockConfigEntry(
        domain=CONST.DOMAIN, data={CONF.PAGES: paths}, options=options
    )
    required = dict.fromkeys(paths, frozenset())
    return {page.key: page.interval for page in polled_pages(entry, required)}


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
        CONST.OPTION_WEBIF_INTERVAL: 2,
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
        "heat_pump": {"Hochdruck": "24.4 BAR", "Verdichter": "2568 rpm"},
        "statistics": {"JAZ Jahr": "4.16"},
        "heating": {"Leistungsbegrenzung": "60 %"},
    }


async def test_a_round_comes_as_often_as_the_most_frequent_page(coordinator):
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
    client.answer(HEAT_PUMP, EMPTY_COLUMN)

    await round_at(coordinator, clock, 0)

    assert client.asked == [HEAT_PUMP.path, STATISTICS.path, HEATING.path]


async def test_one_failure_keeps_the_values_and_the_second_takes_them_away(
    coordinator, client, clock
):
    await round_at(coordinator, clock, 0)
    kept = coordinator.data["heat_pump"]
    client.answer(HEAT_PUMP, EMPTY_COLUMN, EMPTY_COLUMN, WHOLE[HEAT_PUMP.path])

    await round_at(coordinator, clock, 15 * 60)
    assert coordinator.data["heat_pump"] == kept

    await round_at(coordinator, clock, 30 * 60)
    assert coordinator.data["heat_pump"] is None
    assert coordinator.data["statistics"] is not None

    await round_at(coordinator, clock, 45 * 60)
    assert coordinator.data["heat_pump"] == kept


async def test_a_page_never_read_has_no_values_to_keep(coordinator, client, clock):
    client.answer(HEAT_PUMP, EMPTY_COLUMN)

    await round_at(coordinator, clock, 0)

    assert coordinator.data["heat_pump"] is None


async def test_the_third_failure_in_a_row_stops_polling(
    hass, coordinator, client, clock, entry
):
    client.answer(HEAT_PUMP, Unreachable("timeout"))
    for quarter in range(3):
        await round_at(coordinator, clock, quarter * 15 * 60)

    assert not coordinator.last_update_success
    assert coordinator.update_interval is None
    issue = ir.async_get(hass).async_get_issue(
        CONST.DOMAIN, f"{STOPPED_ISSUE}_{entry.entry_id}"
    )
    assert issue is not None
    assert issue.translation_placeholders == {"page": "heat_pump", "error": "timeout"}

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


async def test_a_reload_takes_the_stop_back(hass, entry, client, clock, coordinator):
    client.answer(HEAT_PUMP, Unreachable("timeout"))
    for quarter in range(3):
        await round_at(coordinator, clock, quarter * 15 * 60)

    WebifCoordinator(hass, entry, client, [HEAT_PUMP], clock=clock)

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
