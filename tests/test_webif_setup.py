"""Starting and stopping a pump's web interface entry, against the stand-in.

Marked `e2e`: every test boots a Home Assistant core and loads the
integration.
"""

import asyncio
from itertools import pairwise

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.weishaupt_modbus import webif_coordinator
from custom_components.weishaupt_modbus.config_flow import ConfigFlow
from custom_components.weishaupt_modbus.configentry import HOST_LOCKS, host_lock
from custom_components.weishaupt_modbus.const import CONF, CONST, DEVICES
from custom_components.weishaupt_modbus.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.weishaupt_modbus.webif import client as webif
from custom_components.weishaupt_modbus.webif.discovery import (
    HEAT_PUMP_PAGE,
    HEATING_PAGE,
    STATISTICS_PAGE,
)
from custom_components.weishaupt_modbus.webif_sensor import (
    ANSWER_TIME,
    POWER_LIMIT,
    READ_VALUES,
    TRAFFIC_SENSORS,
    WEBIF_SENSORS,
)
from custom_components.weishaupt_modbus.webif_visit import read_web_interface
from custom_components.weishaupt_modbus.write_counter_sensor import (
    WEBIF_WRITE_COUNTER_DESCRIPTIONS,
)
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, EntityCategory
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed

from .locking import WatchedLock, until
from .webif_stand_in import (
    HEAT_PUMP_INFO,
    HEATING,
    HEATING_PATH,
    INFO,
    PASSWORD,
    PUMP_MENU,
    SAVE_PATH,
    STACK,
    STATISTICS_INFO,
    USER,
    StandInPump,
    column,
    limit_segment,
    link,
    menu_site,
    serving,
    value,
)

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(120)]

PAGES = {
    HEAT_PUMP_PAGE: STACK + f"{INFO},{HEAT_PUMP_INFO}",
    STATISTICS_PAGE: STACK + f"{INFO},{STATISTICS_INFO}",
    HEATING_PAGE: STACK + f"{PUMP_MENU},{HEATING}",
}
SWITCHING_DIFFERENCE = "64001805000000002D40000A0B030011010401"
POWER_LIMIT_SEGMENT = "64001807000000003C40000A0B030011010401"
# What the controller shows, by the unit text after the number.
SAMPLE = {
    "°C": "21.5 °C",
    "K": "4.5 K",
    "BAR": "12.0 BAR",
    "%": "40 %",
    "KW": "4.9 KW",
    "KWh": "5356.310 KWh",
    "m3/h": "1.7 m3/h",
    "rpm": "2568 rpm",
    "h": "16847 h",
    "": "127",
    None: "V3.0",
}
# The values, and the sensors about the polling itself.
EVERY_SENSOR = (*WEBIF_SENSORS, *TRAFFIC_SENSORS, ANSWER_TIME)
# And the one setting it writes, a number, with the counts of its writes.
EVERY_ENTITY = (*EVERY_SENSOR, POWER_LIMIT, *WEBIF_WRITE_COUNTER_DESCRIPTIONS)
SHOWN: dict = {}
for sensor in READ_VALUES:
    SHOWN.setdefault(sensor.page, {})[sensor.title] = SAMPLE[sensor.shown_unit]
HEATING_LINKS = {
    "Schaltdifferenz": SWITCHING_DIFFERENCE,
    "Leistungsbegrenzung": POWER_LIMIT_SEGMENT,
}
SITE = {
    PAGES[HEAT_PUMP_PAGE]: column(
        "".join(value(title, shown) for title, shown in SHOWN[HEAT_PUMP_PAGE].items())
    ),
    PAGES[STATISTICS_PAGE]: column(
        "".join(value(title, shown) for title, shown in SHOWN[STATISTICS_PAGE].items())
    ),
    PAGES[HEATING_PAGE]: column(
        "".join(
            link([PUMP_MENU, HEATING, HEATING_LINKS[title]], title, shown)
            for title, shown in SHOWN[HEATING_PAGE].items()
        )
    ),
}
LOGIN = [("GET", webif.INDEX), ("POST", webif.LOGIN)]
FIRST_ROUND = [*LOGIN, *(("GET", path) for path in PAGES.values())]
# What the pump entry needs besides the address to run its Modbus side.
MODBUS_SIDE = {
    CONF.PORT: 502,
    CONF.KENNFELD_FILE: CONST.DEF_KENNFELDFILE,
    CONF.HK2: False,
    CONF.HK3: False,
    CONF.HK4: False,
    CONF.HK5: False,
}
REDACTED = "**REDACTED**"
GAP = 0.2
# The event loop may wake a sleeper a whisker early by its clock resolution.
CLOCK_TOLERANCE = 0.001


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    return


@pytest.fixture
async def pump(socket_enabled, monkeypatch):
    """The stand-in answering the three pages; the entry asks without pauses."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", 0)
    stand_in = StandInPump()
    stand_in.site = dict(SITE)
    async with serving(stand_in) as served:
        yield served


def _entries(hass, pump, password=PASSWORD, options=None, pump_title="pump"):
    """A pump entry and its web interface; the pump's Modbus side is not under
    test here, so its entry stays disabled. `pump_title` is the one the web
    interface's title was made from; None for an entry made before it was
    kept."""
    pump_entry = MockConfigEntry(
        domain=CONST.DOMAIN,
        data={
            CONF.HOST: pump.host,
            CONF.PREFIX: CONST.DEF_PREFIX,
            CONF.DEVICE_POSTFIX: "",
            CONF.NAME_DEVICE_PREFIX: False,
            CONF.NAME_TOPIC_PREFIX: False,
        },
        version=11,
        disabled_by=ConfigEntryDisabler.USER,
        title="pump",
    )
    pump_entry.add_to_hass(hass)
    data = {
        CONF.KIND: CONST.WEB_INTERFACE,
        CONF.PUMP_ENTRY: pump_entry.entry_id,
        CONF.USERNAME: USER,
        CONF.PASSWORD: password,
        CONF.PAGES: PAGES,
    }
    if pump_title is not None:
        data[CONF.PUMP_TITLE] = pump_title
    web = MockConfigEntry(
        domain=CONST.DOMAIN,
        data=data,
        options=options or {},
        version=11,
        title="pump web interface",
    )
    web.add_to_hass(hass)
    return web


async def _start(hass, entry):
    """Set the entry up and wait for its first round, which runs after it."""
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


@pytest.fixture
async def other_pump(pump):
    """A second stand-in, at another address."""
    stand_in = StandInPump()
    stand_in.site = dict(SITE)
    async with serving(stand_in) as served:
        yield served


async def _run_the_pump(hass, web, mock_modbus):
    """Enable the web interface's pump entry, with what its Modbus side needs."""
    mock_modbus.load_raw({"input": {30001: 123}})
    pump_entry = hass.config_entries.async_get_entry(web.data[CONF.PUMP_ENTRY])
    hass.config_entries.async_update_entry(
        pump_entry, data={**pump_entry.data, **MODBUS_SIDE}
    )
    await hass.config_entries.async_set_disabled_by(pump_entry.entry_id, None)
    await hass.async_block_till_done()
    assert pump_entry.state is ConfigEntryState.LOADED


async def test_a_web_interface_starts_with_a_round_of_its_pages(hass, pump):
    entry = await _start(hass, _entries(hass, pump))

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator.data == SHOWN
    assert pump.asked == [*LOGIN, *(("GET", path) for path in PAGES.values())]


async def test_the_start_does_not_wait_for_the_first_round(hass, pump):
    """Live, the first round took 25 to 45 s, and Home Assistant's start
    waited for all of it."""
    pump.delays[webif.INDEX] = 0.5
    entry = _entries(hass, pump)

    assert await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.LOADED
    assert ("GET", PAGES[HEAT_PUMP_PAGE]) not in pump.asked
    assert hass.states.get(_sensor(hass, "hochdruck")).state == "unavailable"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(_sensor(hass, "hochdruck")).state == "12.0"


async def test_a_session_dropped_right_after_the_login_ends_the_round(hass, pump):
    """Live, 2026-10-02 and 03, with the quoted cookie: the controller drops
    every fresh session. One new login per round, not one per page."""
    pump.keeps_sessions = False

    await _start(hass, _entries(hass, pump))

    assert pump.asked.count(("POST", webif.LOGIN)) == 2
    assert pump.asked.count(("GET", PAGES[STATISTICS_PAGE])) == 0


async def test_a_first_round_failing_leaves_no_session_behind(hass, pump, monkeypatch):
    """Each retry of a setup that failed after its login left one more
    session open on the controller. The round now fails after the setup:
    no retry, one session, and the unload logs it out."""

    def unreadable(self, text):
        raise ValueError("a page layout nobody expected")

    monkeypatch.setattr(webif_coordinator.Page, "read", unreadable)

    entry = await _start(hass, _entries(hass, pump))

    assert entry.state is ConfigEntryState.LOADED
    assert len(pump.sessions) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert pump.sessions == set()


async def test_an_update_asked_for_during_the_first_round_adds_no_round(hass, pump):
    """The first round runs beside the start: an update asked for meanwhile
    would ask the pages it has not reached yet a second time."""
    pump.delays[webif.INDEX] = 0.5
    entry = _entries(hass, pump)
    assert await hass.config_entries.async_setup(entry.entry_id)

    await entry.runtime_data.coordinator.async_request_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert pump.asked == FIRST_ROUND


async def test_unloading_stops_and_logs_out(hass, pump):
    entry = await _start(hass, _entries(hass, pump))

    assert await hass.config_entries.async_unload(entry.entry_id)

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert pump.asked[-1] == ("GET", webif.LOGOUT)


async def test_stopping_home_assistant_leaves_no_session_open(hass, pump):
    """Live, 2026-10-03: Home Assistant stops without unloading its entries,
    so every restart left a session open on the controller."""
    await _start(hass, _entries(hass, pump))

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert pump.asked[-1] == ("GET", webif.LOGOUT)
    assert pump.sessions == set()


async def test_the_dialog_and_the_running_entry_keep_the_gap_between_them(
    hass, pump, monkeypatch
):
    """Live, 2026-10-03: a reconfigure's visit and the entry's round asked
    the pump 0.8 s apart."""
    monkeypatch.setattr(webif, "MIN_GAP_SECONDS", GAP)
    pump.site.update(menu_site())
    entry = await _start(hass, _entries(hass, pump))
    pump.arrivals.clear()

    await asyncio.gather(
        entry.runtime_data.client.page(PAGES[HEAT_PUMP_PAGE], bool),
        read_web_interface(hass, pump.host, USER, PASSWORD),
    )

    gaps = [later - earlier for earlier, later in pairwise(sorted(pump.arrivals))]
    assert min(gaps) >= GAP - CLOCK_TOLERANCE


async def test_unloading_after_the_stop_logs_no_error(hass, pump, caplog):
    """A one-time stop listener removes itself when it fires; removing it
    again on unload logged an error."""
    entry = await _start(hass, _entries(hass, pump))
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert "Unable to remove" not in caplog.text


async def test_a_refused_login_asks_for_a_new_one(hass, pump):
    """The first round runs after the setup, so the entry stays loaded."""
    entry = await _start(hass, _entries(hass, pump, password="outdated"))

    assert entry.state is ConfigEntryState.LOADED
    flows = hass.config_entries.flow.async_progress_by_handler(CONST.DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]
    assert pump.asked == LOGIN


async def test_a_web_interface_comes_through_a_migration_as_it_is(hass, pump):
    """The migration read every entry as a pump and asked it for its host, so
    the next version step would have stopped every web interface."""
    entry = _entries(hass, pump)
    own_id = f"{entry.data[CONF.PUMP_ENTRY]}-{CONST.WEB_INTERFACE}"
    hass.config_entries.async_update_entry(entry, minor_version=0, unique_id=own_id)
    kept = dict(entry.data)

    await _start(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    current = (ConfigFlow.VERSION, ConfigFlow.MINOR_VERSION)
    assert (entry.version, entry.minor_version) == current
    assert dict(entry.data) == kept
    assert entry.unique_id == own_id


async def test_a_web_interface_without_its_pump_does_not_start(hass, pump):
    entry = _entries(hass, pump)
    await hass.config_entries.async_remove(entry.data[CONF.PUMP_ENTRY])

    await _start(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert "removed" in entry.reason
    assert pump.asked == []


@pytest.mark.parametrize("running", [False, True], ids=["pump_off", "pump_running"])
async def test_a_web_interface_follows_its_pump_to_a_new_address(
    hass, pump, other_pump, mock_modbus, running
):
    """The pump entry's reconfigure gave it a new address; the web interface
    kept polling the old one, under a lock Modbus no longer took."""
    web = await _start(hass, _entries(hass, pump))
    if running:
        await _run_the_pump(hass, web, mock_modbus)
    pump_entry = hass.config_entries.async_get_entry(web.data[CONF.PUMP_ENTRY])

    hass.config_entries.async_update_entry(
        pump_entry, data={**pump_entry.data, CONF.HOST: other_pump.host}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert pump.asked[-1] == ("GET", webif.LOGOUT)
    assert other_pump.asked == FIRST_ROUND
    assert web.runtime_data.client._host_lock is host_lock(hass, other_pump.host)


async def test_a_web_interface_named_after_its_pump_takes_its_new_address(
    hass, pump, other_pump
):
    """The pump's reconfigure titled it by its new address; its web interface
    kept the old one in its title."""
    web = await _start(hass, _entries(hass, pump))
    pump_entry = hass.config_entries.async_get_entry(web.data[CONF.PUMP_ENTRY])

    hass.config_entries.async_update_entry(
        pump_entry,
        title=other_pump.host,
        data={**pump_entry.data, CONF.HOST: other_pump.host},
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert web.title == f"{other_pump.host} web interface"


async def test_a_web_interface_named_after_its_pump_follows_a_new_name(hass, pump):
    """Every new name, not only the first. A name alone changes nothing the
    web interface asks with, so it is not logged out and in again for it."""
    web = await _start(hass, _entries(hass, pump))
    pump_entry = hass.config_entries.async_get_entry(web.data[CONF.PUMP_ENTRY])
    pump.asked.clear()

    for name in ("cellar", "basement"):
        hass.config_entries.async_update_entry(pump_entry, title=name)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert web.title == "basement web interface"
    assert pump.asked == []


async def test_a_web_interface_named_after_its_pump_follows_a_name_given_while_off(
    hass, pump
):
    """Renamed while the web interface was switched off, the pump's new title
    became the one it was named after, and its old title counted as one of
    its own: it never followed again."""
    web = await _start(hass, _entries(hass, pump))
    pump_entry = hass.config_entries.async_get_entry(web.data[CONF.PUMP_ENTRY])
    await hass.config_entries.async_set_disabled_by(
        web.entry_id, ConfigEntryDisabler.USER
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    hass.config_entries.async_update_entry(pump_entry, title="cellar")
    await hass.config_entries.async_set_disabled_by(web.entry_id, None)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert web.title == "cellar web interface"

    hass.config_entries.async_update_entry(pump_entry, title="basement")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert web.title == "basement web interface"


async def test_a_web_interface_named_after_its_pump_follows_a_name_given_in_its_reload(
    hass, pump, other_pump
):
    """A new address reloads the web interface; a name given in the same
    moment came while it reloaded, and it kept the old one for good."""
    web = await _start(hass, _entries(hass, pump))
    pump_entry = hass.config_entries.async_get_entry(web.data[CONF.PUMP_ENTRY])

    hass.config_entries.async_update_entry(
        pump_entry, data={**pump_entry.data, CONF.HOST: other_pump.host}
    )
    hass.config_entries.async_update_entry(pump_entry, title="cellar")
    await hass.async_block_till_done(wait_background_tasks=True)

    assert web.title == "cellar web interface"


async def test_a_web_interface_made_before_its_pump_title_was_kept_still_follows(
    hass, pump
):
    """Without the title it was made from, it follows while it runs."""
    web = await _start(hass, _entries(hass, pump, pump_title=None))
    pump_entry = hass.config_entries.async_get_entry(web.data[CONF.PUMP_ENTRY])

    hass.config_entries.async_update_entry(pump_entry, title="cellar")
    await hass.async_block_till_done(wait_background_tasks=True)

    assert web.title == "cellar web interface"


async def test_a_web_interface_given_a_name_of_its_own_keeps_it(hass, pump, other_pump):
    web = _entries(hass, pump)
    hass.config_entries.async_update_entry(web, title="Weboberfläche Keller")
    await _start(hass, web)
    pump_entry = hass.config_entries.async_get_entry(web.data[CONF.PUMP_ENTRY])

    hass.config_entries.async_update_entry(
        pump_entry,
        title=other_pump.host,
        data={**pump_entry.data, CONF.HOST: other_pump.host},
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert web.title == "Weboberfläche Keller"


async def test_switching_its_pump_on_and_off_leaves_the_web_interface_alone(
    hass, pump, mock_modbus
):
    """Only the pump's data matter to its web interface: switching the pump
    on or off must not log the web interface out and in again."""
    mock_modbus.load_raw({"input": {30001: 123}})
    web = _entries(hass, pump)
    pump_entry = hass.config_entries.async_get_entry(web.data[CONF.PUMP_ENTRY])
    hass.config_entries.async_update_entry(
        pump_entry, data={**pump_entry.data, **MODBUS_SIDE}
    )
    await _start(hass, web)
    pump.asked.clear()

    for disabled_by in (None, ConfigEntryDisabler.USER):
        await hass.config_entries.async_set_disabled_by(
            pump_entry.entry_id, disabled_by
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert pump.asked == []
    assert web.state is ConfigEntryState.LOADED


async def test_a_web_interface_stops_when_its_pump_is_removed(hass, pump):
    """The deleted pump entry left its web interface polling, and a pump added
    again with its web interface got no sensors: their ids were still taken."""
    web = await _start(hass, _entries(hass, pump))

    await hass.config_entries.async_remove(web.data[CONF.PUMP_ENTRY])
    await hass.async_block_till_done()

    assert web.state is ConfigEntryState.SETUP_ERROR
    assert pump.asked[-1] == ("GET", webif.LOGOUT)
    again = await _start(hass, _entries(hass, pump))
    registry = er.async_get(hass)
    assert len(er.async_entries_for_config_entry(registry, again.entry_id)) == len(
        EVERY_ENTITY
    )


def _next_round_within(entry, minutes):
    """Whether the round after the first comes within the minutes, less the
    seconds the first round took to reach that page."""
    wait = entry.runtime_data.coordinator.update_interval.total_seconds()
    return minutes * 60 - webif_coordinator.DUE_SLACK_SECONDS < wait <= minutes * 60


async def test_the_heat_pump_page_follows_the_interval_option(hass, pump):
    """7 minutes beside the other pages' 15: the next round is the heat pump
    page's, 7 minutes after the first."""
    entry = await _start(
        hass, _entries(hass, pump, options={CONST.OPTION_WEBIF_HEAT_PUMP_INTERVAL: 7})
    )

    assert _next_round_within(entry, 7)


async def test_a_statistics_interval_below_the_heat_pump_page_sets_the_rounds(
    hass, pump
):
    options = {
        CONST.OPTION_WEBIF_HEAT_PUMP_INTERVAL: 15,
        CONST.OPTION_WEBIF_STATISTICS_INTERVAL: 3,
    }
    entry = await _start(hass, _entries(hass, pump, options=options))

    assert _next_round_within(entry, 3)


async def test_the_web_interface_waits_while_its_pump_is_asked(hass, pump):
    """Modbus and the web interface of one pump never ask it at once."""
    entry = _entries(hass, pump)
    lock = WatchedLock()
    hass.data.setdefault(HOST_LOCKS, {})[pump.host] = lock
    pump.watched_lock = lock

    async with lock:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await until(lambda: lock.waited or pump.asked)
        assert pump.asked == []
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.state is ConfigEntryState.LOADED
    assert pump.asked == FIRST_ROUND
    assert all(pump.held), "the entry asked without its pump's lock"


async def test_the_diagnostics_leave_out_the_login_and_the_page_addresses(hass, pump):
    """And the pump title the entry's own was made from: free text, often a
    room or a name."""
    entry = await _start(hass, _entries(hass, pump))

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    data = diagnostics["entry"]["data"]
    assert {
        data[CONF.USERNAME],
        data[CONF.PASSWORD],
        data[CONF.PAGES],
        data[CONF.PUMP_TITLE],
    } == {REDACTED}
    assert PAGES[HEATING_PAGE] not in str(diagnostics)
    coordinator = diagnostics["coordinator"]
    assert coordinator["pages"][STATISTICS_PAGE] == SHOWN[STATISTICS_PAGE]
    assert coordinator["traffic"]["requests"] == len(FIRST_ROUND)
    assert coordinator["page_states"][HEAT_PUMP_PAGE]["failures"] == 0
    assert coordinator["stopped_by"] is None


async def test_the_values_become_sensors_on_a_device_of_their_own(hass, pump):
    entry = await _start(hass, _entries(hass, pump))
    registry = er.async_get(hass)

    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == len(
        EVERY_ENTITY
    )
    high_pressure = registry.async_get_entity_id(
        "sensor", CONST.DOMAIN, f"{CONST.DEF_PREFIX}webif_hochdruck"
    )
    state = hass.states.get(high_pressure)
    assert (state.state, state.attributes["unit_of_measurement"]) == ("12.0", "bar")
    device = dr.async_get(hass).async_get(registry.async_get(high_pressure).device_id)
    assert (CONST.DOMAIN, DEVICES.WEBIF) in device.identifiers


async def test_a_pump_run_once_and_then_disabled_still_shows_its_web_interface(
    hass, pump, mock_modbus
):
    """The order a second Home Assistant takes beside a running one: the pump
    entry runs once, so its devices exist, and is disabled to stop asking
    over Modbus; then the web interface is added. Its device is its own, so
    the pump entry's disabled devices do not take its sensors along."""
    mock_modbus.load_raw({"input": {30001: 123}})
    pump_entry = MockConfigEntry(
        domain=CONST.DOMAIN,
        data={
            CONF.HOST: pump.host,
            CONF.PORT: 502,
            CONF.PREFIX: CONST.DEF_PREFIX,
            CONF.DEVICE_POSTFIX: "",
            CONF.KENNFELD_FILE: CONST.DEF_KENNFELDFILE,
            CONF.HK2: False,
            CONF.HK3: False,
            CONF.HK4: False,
            CONF.HK5: False,
            CONF.NAME_DEVICE_PREFIX: False,
            CONF.NAME_TOPIC_PREFIX: False,
        },
        version=11,
        title="pump",
    )
    pump_entry.add_to_hass(hass)
    await _start(hass, pump_entry)
    assert pump_entry.state is ConfigEntryState.LOADED
    await hass.config_entries.async_set_disabled_by(
        pump_entry.entry_id, ConfigEntryDisabler.USER
    )
    await hass.async_block_till_done()
    web = MockConfigEntry(
        domain=CONST.DOMAIN,
        data={
            CONF.KIND: CONST.WEB_INTERFACE,
            CONF.PUMP_ENTRY: pump_entry.entry_id,
            CONF.USERNAME: USER,
            CONF.PASSWORD: PASSWORD,
            CONF.PAGES: PAGES,
        },
        version=11,
        title="pump web interface",
    )
    web.add_to_hass(hass)

    await _start(hass, web)

    registry = er.async_get(hass)
    high_pressure = registry.async_get_entity_id(
        "sensor", CONST.DOMAIN, f"{CONST.DEF_PREFIX}webif_hochdruck"
    )
    assert registry.async_get(high_pressure).disabled_by is None
    assert hass.states.get(high_pressure).state == "12.0"
    device = dr.async_get(hass).async_get(registry.async_get(high_pressure).device_id)
    assert device.config_entry_id == web.entry_id
    assert device.disabled_by is None


TRAFFIC_COUNTS = ("requests", "logins", "incomplete_pages", "failed_reads")


def _sensor(hass, key):
    return er.async_get(hass).async_get_entity_id(
        "sensor", CONST.DOMAIN, f"{CONST.DEF_PREFIX}webif_{key}"
    )


async def test_the_polling_shows_on_diagnostic_sensors(hass, pump):
    """User wish, 2026-10-03: whether the access gets worse over time shows
    in Home Assistant's statistics of these sensors."""
    await _start(hass, _entries(hass, pump))
    registry = er.async_get(hass)

    counts = {key: hass.states.get(_sensor(hass, key)).state for key in TRAFFIC_COUNTS}
    assert counts == {
        "requests": "5",
        "logins": "1",
        "incomplete_pages": "0",
        "failed_reads": "0",
    }
    answer = hass.states.get(_sensor(hass, "answer_time"))
    assert float(answer.state) >= 0
    assert answer.attributes["unit_of_measurement"] == "s"
    web_device = registry.async_get(_sensor(hass, "hochdruck")).device_id
    for key in (*TRAFFIC_COUNTS, "answer_time"):
        registered = registry.async_get(_sensor(hass, key))
        assert registered.entity_category is EntityCategory.DIAGNOSTIC
        assert registered.device_id == web_device


async def test_the_counts_run_on_across_a_restart(hass, pump):
    """User decision, 2026-10-03: the counts carry over a restart."""
    web = _entries(hass, pump)
    er.async_get(hass).async_get_or_create(
        "sensor",
        CONST.DOMAIN,
        f"{CONST.DEF_PREFIX}webif_requests",
        config_entry=web,
        suggested_object_id="web_requests",
    )
    recorded = {"native_value": 100, "native_unit_of_measurement": None}
    mock_restore_cache_with_extra_data(
        hass, [(State("sensor.web_requests", "100"), recorded)]
    )

    await _start(hass, web)

    assert hass.states.get("sensor.web_requests").state == str(100 + len(FIRST_ROUND))


async def test_the_diagnostic_sensors_stay_shown_when_polling_fails(hass, pump):
    """The values go unavailable after the brake; the counts explain why."""
    entry = await _start(hass, _entries(hass, pump))

    entry.runtime_data.coordinator.async_set_update_error(UpdateFailed("stopped"))
    await hass.async_block_till_done()

    assert hass.states.get(_sensor(hass, "hochdruck")).state == "unavailable"
    for key in (*TRAFFIC_COUNTS, "answer_time"):
        assert hass.states.get(_sensor(hass, key)).state != "unavailable"


WEB_WRITE_COUNTERS = ("settings_written_total", "settings_written_today")


async def test_the_web_interface_counts_its_written_settings(hass, pump):
    """Beside the polling counts, on the web interface's own device: the
    EEPROM counters of the pump entry count Modbus writes only."""
    entry = await _start(hass, _entries(hass, pump))
    registry = er.async_get(hass)
    web_device = registry.async_get(_sensor(hass, "hochdruck")).device_id

    for key in WEB_WRITE_COUNTERS:
        counter = registry.async_get(_sensor(hass, key))
        assert counter.device_id == web_device
        assert counter.entity_category is EntityCategory.DIAGNOSTIC
        assert hass.states.get(counter.entity_id).state == "0"

    entry.runtime_data.coordinator.budget.record_write()
    await hass.async_block_till_done()

    for key in WEB_WRITE_COUNTERS:
        assert hass.states.get(_sensor(hass, key)).state == "1"


async def test_the_web_write_counters_carry_over_a_reload(hass, pump):
    entry = await _start(hass, _entries(hass, pump))
    entry.runtime_data.coordinator.budget.record_write()
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    for key in WEB_WRITE_COUNTERS:
        assert hass.states.get(_sensor(hass, key)).state == "1", key
    assert entry.runtime_data.coordinator.budget.total == 1


@pytest.mark.parametrize(("chosen", "limit"), [(None, 10), (1, 1), (0, 0)])
async def test_the_option_sets_the_daily_limit(hass, pump, chosen, limit):
    options = {} if chosen is None else {CONST.OPTION_WEBIF_WRITE_LIMIT_PER_DAY: chosen}

    entry = await _start(hass, _entries(hass, pump, options=options))

    assert entry.runtime_data.coordinator.budget.limit == limit


# The quiet time a write waits for, shortened for these tests.
QUIET = 0.2
POWER_LIMIT_UNIQUE_ID = f"{CONST.DEF_PREFIX}webif_{POWER_LIMIT.key}"


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(webif_coordinator, "DEBOUNCE_SECONDS", QUIET)


def _number(hass):
    return er.async_get(hass).async_get_entity_id(
        "number", CONST.DOMAIN, POWER_LIMIT_UNIQUE_ID
    )


async def _set_power_limit(hass, value):
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _number(hass), "value": value},
        blocking=True,
    )


async def test_the_power_limit_is_set_from_home_assistant(hass, pump, quick):
    """Live, 2026-10-02, the same four requests: the heating page, the
    limit's own page, one save, the heating page again."""
    pump.show_power_limit(60)
    await _start(hass, _entries(hass, pump))
    assert hass.states.get(_number(hass)).state == "60"
    asked = len(pump.asked)

    await _set_power_limit(hass, 61)

    assert [form["value"] for form in pump.saved] == ["61"]
    assert hass.states.get(_number(hass)).state == "61"
    assert hass.states.get(_sensor(hass, "settings_written_today")).state == "1"
    assert pump.asked[asked:] == [
        ("GET", HEATING_PATH),
        ("GET", f"{HEATING_PATH},{limit_segment(60)}"),
        ("POST", SAVE_PATH),
        ("GET", HEATING_PATH),
    ]


async def test_settings_made_at_once_are_one_write(hass, pump, quick):
    """Arrow clicks are calls that overlap: Home Assistant queued them one
    behind the other, and each became a write of its own."""
    pump.show_power_limit(60)
    await _start(hass, _entries(hass, pump))

    await asyncio.gather(*(_set_power_limit(hass, value) for value in (61, 62, 63)))

    assert [form["value"] for form in pump.saved] == ["63"]


async def test_a_refused_power_limit_reaches_the_caller(hass, pump, quick):
    pump.show_power_limit(60)
    options = {CONST.OPTION_WEBIF_WRITE_LIMIT_PER_DAY: 1}
    await _start(hass, _entries(hass, pump, options=options))
    await _set_power_limit(hass, 61)

    with pytest.raises(HomeAssistantError) as refused:
        await _set_power_limit(hass, 62)

    assert refused.value.translation_key == "webif_write_limit_reached"
    assert len(pump.saved) == 1
    assert hass.states.get(_number(hass)).state == "61"


async def test_the_old_power_limit_sensor_leaves_no_entry(hass, pump):
    """Until 2.1.0b2 the power limit was a sensor; left in the registry it
    would show unavailable for good beside the number."""
    web = _entries(hass, pump)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", CONST.DOMAIN, POWER_LIMIT_UNIQUE_ID, config_entry=web
    )

    await _start(hass, web)

    assert (
        registry.async_get_entity_id("sensor", CONST.DOMAIN, POWER_LIMIT_UNIQUE_ID)
        is None
    )
    assert _number(hass) is not None


async def test_an_unload_during_a_save_tells_its_caller_and_asks_nothing_more(
    hass, pump, quick
):
    """The save itself goes through; nothing follows the logout, and the
    caller hears that the write was called off."""
    pump.show_power_limit(60)
    entry = await _start(hass, _entries(hass, pump))
    pump.delays[SAVE_PATH] = 0.5
    setting = asyncio.create_task(_set_power_limit(hass, 61))
    await until(lambda: ("POST", SAVE_PATH) in pump.asked)

    assert await hass.config_entries.async_unload(entry.entry_id)

    with pytest.raises(HomeAssistantError) as called_off:
        await setting
    assert called_off.value.translation_key == "webif_write_aborted"
    assert pump.asked[-1] == ("GET", webif.LOGOUT)


async def test_the_power_limit_is_set_without_a_reading_of_the_heating_page(
    hass, pump, quick
):
    """The first round could not read the heating page; the call reached
    nothing while the number was unavailable."""
    pump.show_power_limit(60)
    pump.failing_once[HEATING_PATH] = 500
    await _start(hass, _entries(hass, pump))
    assert hass.states.get(_number(hass)).state == "unknown"

    await _set_power_limit(hass, 61)

    assert [form["value"] for form in pump.saved] == ["61"]
    assert hass.states.get(_number(hass)).state == "61"
