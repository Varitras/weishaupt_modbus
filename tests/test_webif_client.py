"""How the client talks to the web interface, against a stand-in on loopback."""

import asyncio
from itertools import pairwise
import logging
import re
import socket

import aiohttp
from aiohttp import web
import pytest

from custom_components.weishaupt_modbus.webif import client as webif

from .webif_stand_in import (
    HEATING,
    PAGE_PATH,
    PASSWORD,
    PUMP_MENU,
    SESSION_ID,
    STACK,
    USER,
    WHOLE,
    StandInPump,
    see_other,
    serving,
)

PAGE = STACK + f"{PUMP_MENU},{HEATING}"
BROKEN = "<p>broken</p>"
LOGIN = [("GET", webif.INDEX), ("POST", webif.LOGIN)]
GAP = 0.2
# The event loop may wake a sleeper a whisker early by its clock resolution.
CLOCK_TOLERANCE = 0.001
SHORT_TIMEOUT = 0.1
SLOW = 0.5


def whole(text):
    return text == WHOLE


@pytest.fixture
async def pump(socket_enabled):
    """A stand-in pump on the loopback address."""
    async with serving(StandInPump()) as stand_in:
        yield stand_in


@pytest.fixture
async def session():
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        yield session


def connect(session, host, password=PASSWORD, gap=0, **options):
    """A client with a lock and a pacing of its own and without the gap
    between requests, unless a test asks otherwise."""
    options.setdefault("host_lock", asyncio.Lock())
    options.setdefault("pacing", webif.Pacing())
    return webif.Client(session, host, USER, password, gap=gap, **options)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


async def test_a_page_is_read_after_a_login(pump, session):
    client = connect(session, pump.host)

    assert await client.page(PAGE, whole) == WHOLE
    assert pump.asked == [*LOGIN, ("GET", PAGE)]
    assert pump.forms == [{"user": USER, "pass": PASSWORD}]


async def test_every_request_is_logged_but_never_the_login(pump, session, caplog):
    """Live, 2026-10-03: a round took 15 s, and the log could not tell a slow
    answer from a page asked for twice. The login and the session id stay out
    of it."""
    caplog.set_level(logging.DEBUG, logger=webif.__name__)
    client = connect(session, pump.host)

    await client.page(PAGE, whole)

    lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == webif.__name__
    ]
    assert [line.split(": ", 1)[0] for line in lines] == [
        f"GET {webif.INDEX}",
        f"POST {webif.LOGIN}",
        f"GET {PAGE}",
    ]
    assert lines[1].startswith(f"POST {webif.LOGIN}: HTTP 303 to {webif.LOGIN_TARGET}")
    assert lines[2].startswith(f"GET {PAGE}: HTTP 200,")
    assert USER not in caplog.text
    assert PASSWORD not in caplog.text
    assert SESSION_ID not in caplog.text


async def test_wrong_credentials_end_the_round_at_the_login(pump, session):
    client = connect(session, pump.host, password="wrong")

    with pytest.raises(webif.LoginRefused):
        await client.page(PAGE, whole)
    assert pump.asked == LOGIN


async def test_a_session_id_with_a_slash_keeps_its_session(pump, session):
    """Live, 2026-10-02 and 03: the controller's session ids carry + and /,
    aiohttp's cookie jar sent one with a slash back in quotes, and the
    controller sent the next page to the login. Most logins failed so."""
    client = connect(session, pump.host)

    assert await client.page(PAGE, whole) == WHOLE
    assert "/" in pump.sessions.pop()


async def test_a_session_that_keeps_cookies_is_refused():
    """Its jar would send the session id back changed."""
    async with aiohttp.ClientSession() as keeping:
        with pytest.raises(TypeError):
            connect(keeping, "127.0.0.1")


async def test_a_login_answer_without_a_session_cookie_is_no_login(pump, session):
    """Every page would come back as a redirect to the login."""
    pump.sets_cookie = False
    client = connect(session, pump.host)

    with pytest.raises(webif.Unreachable):
        await client.page(PAGE, whole)
    assert pump.asked == LOGIN


def database_unavailable():
    return see_other("/index.html#nocon")


def error_status():
    return web.Response(status=500)


def empty_session_cookie():
    answer = see_other(webif.LOGIN_TARGET)
    answer.headers["Set-Cookie"] = f"{webif.SESSION_COOKIE}=; path=/"
    return answer


@pytest.mark.parametrize(
    "answer",
    [database_unavailable, error_status, empty_session_cookie],
    ids=lambda answer: answer.__name__,
)
async def test_a_login_the_controller_cannot_serve_is_no_refusal(pump, session, answer):
    """Every login answer but the wrong password counted as one: a controller
    without its database asked the user for the credentials it already had."""
    pump.login_answer = answer
    client = connect(session, pump.host)

    with pytest.raises(webif.Unreachable):
        await client.page(PAGE, whole)
    assert pump.asked == LOGIN


async def test_a_page_that_came_whole_but_wrong_is_asked_for_once_more(pump, session):
    pump.pages = [BROKEN, WHOLE]
    client = connect(session, pump.host)

    assert await client.page(PAGE, whole) == WHOLE
    assert pump.asked == [*LOGIN, ("GET", PAGE), ("GET", PAGE)]


async def test_a_page_wrong_twice_is_broken(pump, session):
    pump.pages = [BROKEN]
    client = connect(session, pump.host)

    with pytest.raises(webif.Broken):
        await client.page(PAGE, whole)
    assert pump.asked == [*LOGIN, ("GET", PAGE), ("GET", PAGE)]


async def test_an_error_status_is_no_page_and_gets_no_second_try(pump, session):
    pump.status = 500
    client = connect(session, pump.host)

    with pytest.raises(webif.Broken):
        await client.page(PAGE, whole)
    assert pump.asked == [*LOGIN, ("GET", PAGE)]


async def test_after_a_timeout_nothing_follows(pump, session):
    pump.delays[PAGE] = SLOW
    client = connect(session, pump.host, timeout=SHORT_TIMEOUT)

    with pytest.raises(webif.Unreachable):
        await client.page(PAGE, whole)
    assert pump.asked == [*LOGIN, ("GET", PAGE)]


async def test_a_dropped_connection_is_not_asked_again(socket_enabled, session):
    """aiohttp sent a GET once more by itself when the connection dropped
    before the answer: a second request at once, outside the gap, to a
    controller that had just struggled."""
    arrivals = []

    async def drop(reader, writer):
        request = await reader.readuntil(b"\r\n\r\n")
        arrivals.append(request.split(b" ", 2)[1].decode())
        writer.close()

    async with await asyncio.start_server(drop, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(webif.Unreachable):
            await connect(session, f"127.0.0.1:{port}").page(PAGE, whole)
    assert arrivals == [webif.INDEX]


async def test_a_pump_that_takes_no_connection_is_unreachable(socket_enabled, session):
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
    client = connect(session, f"127.0.0.1:{port}")

    with pytest.raises(webif.Unreachable) as excinfo:
        await client.page(PAGE, whole)
    # The message reaches the log and the repair notice; the pump's address
    # stays out of both.
    assert "127.0.0.1" not in str(excinfo.value)


async def test_a_lost_session_is_renewed_once(pump, session):
    client = connect(session, pump.host)
    await client.page(PAGE, whole)
    pump.sessions.clear()
    pump.asked.clear()

    assert await client.page(PAGE, whole) == WHOLE
    assert pump.asked == [("GET", PAGE), *LOGIN, ("GET", PAGE)]


async def test_a_session_lost_again_right_after_the_login_is_broken(pump, session):
    """No login loop: one new login per round, whatever the server does."""
    pump.keeps_sessions = False
    client = connect(session, pump.host)

    with pytest.raises(webif.Broken):
        await client.page(PAGE, whole)
    assert pump.asked == [*LOGIN, ("GET", PAGE), *LOGIN, ("GET", PAGE)]


async def test_a_redirected_page_names_where_it_was_sent(pump, session):
    """Live, 2026-10-03: a page after an accepted login failed with "HTTP 303"
    alone, which left open where the controller sent it."""
    pump.keeps_sessions = False
    client = connect(session, pump.host)

    with pytest.raises(webif.Broken, match=re.escape(f"HTTP 303 to {webif.INDEX}")):
        await client.page(PAGE, whole)


async def test_the_session_is_renewed_after_a_day(pump, session):
    clock = FakeClock()
    client = connect(session, pump.host, clock=clock)
    await client.page(PAGE, whole)

    clock.now = webif.SESSION_LIFETIME_SECONDS - 1
    pump.asked.clear()
    await client.page(PAGE, whole)
    assert pump.asked == [("GET", PAGE)]

    clock.now = webif.SESSION_LIFETIME_SECONDS
    pump.asked.clear()
    await client.page(PAGE, whole)
    assert pump.asked == [("GET", webif.LOGOUT), *LOGIN, ("GET", PAGE)]


async def test_a_logout_that_times_out_ends_the_round_before_the_new_login(
    pump, session
):
    """Nothing follows a timeout, not even the login of the daily renewal."""
    clock = FakeClock()
    client = connect(session, pump.host, clock=clock, timeout=SHORT_TIMEOUT)
    await client.page(PAGE, whole)
    clock.now = webif.SESSION_LIFETIME_SECONDS
    pump.delays[webif.LOGOUT] = SLOW
    pump.asked.clear()

    with pytest.raises(webif.Unreachable):
        await client.page(PAGE, whole)
    assert pump.asked == [("GET", webif.LOGOUT)]

    pump.delays.clear()
    pump.asked.clear()
    await client.page(PAGE, whole)
    assert pump.asked == [*LOGIN, ("GET", PAGE)]


async def test_closing_logs_out_once_and_only_when_logged_in(pump, session):
    await connect(session, pump.host).close()
    assert pump.asked == []

    client = connect(session, pump.host)
    await client.page(PAGE, whole)
    pump.asked.clear()
    await client.close()
    await client.close()
    assert pump.asked == [("GET", webif.LOGOUT)]


async def test_a_round_running_at_close_logs_in_no_more(pump, session):
    """The entry unloads or Home Assistant stops while a round still runs: a
    page after the logout would log in again, and that session would stay
    open on the controller."""
    client = connect(session, pump.host)
    await client.page(PAGE, whole)
    pump.delays[PAGE] = SLOW
    pump.asked.clear()
    reading = asyncio.create_task(client.page(PAGE, whole))
    while not pump.asked:
        await asyncio.sleep(0.01)

    await client.close()
    assert await reading == WHOLE
    with pytest.raises(webif.WebifError):
        await client.page(PAGE, whole)
    assert pump.asked == [("GET", PAGE), ("GET", webif.LOGOUT)]


async def test_a_logout_without_an_answer_does_not_fail_the_close(pump, session):
    client = connect(session, pump.host, timeout=SHORT_TIMEOUT)
    await client.page(PAGE, whole)
    pump.delays[webif.LOGOUT] = SLOW

    await client.close()
    assert pump.asked[-1] == ("GET", webif.LOGOUT)


@pytest.mark.parametrize(
    "path",
    [
        PAGE + "&access_code=0000",
        PAGE_PATH + "?access_code=0000",
        PAGE_PATH + "?stack=" + "0" * 37,
        "/pro_save.html",
    ],
)
async def test_an_address_off_the_positive_list_is_never_asked_for(pump, session, path):
    """The access code form on every page saves by GET."""
    client = connect(session, pump.host)

    with pytest.raises(ValueError, match="positive list"):
        await client.page(path, whole)
    assert pump.asked == LOGIN


@pytest.mark.parametrize(
    ("method", "path", "allowed"),
    [
        ("POST", webif.LOGIN, True),
        ("POST", "/pro_save.html", False),
        ("POST", PAGE, False),
        ("GET", PAGE, True),
        ("GET", webif.OVERVIEW, True),
        ("GET", webif.LOGIN, False),
    ],
)
def test_no_post_but_the_login(method, path, allowed):
    """A POST elsewhere would be a write, and a write needs its own entry."""
    assert webif._allowed(method, path) is allowed


async def test_requests_keep_their_distance(pump, session):
    client = connect(session, pump.host, gap=GAP)

    await client.page(PAGE, whole)

    gaps = [later - earlier for earlier, later in pairwise(pump.arrivals)]
    assert len(gaps) == 2
    assert min(gaps) >= GAP - CLOCK_TOLERANCE


async def test_two_clients_of_one_pump_keep_the_gap_between_them(pump, session):
    """Live, 2026-10-03: the dialog's visit and the entry's round asked the
    pump 0.8 s apart, and the two logouts of a reload 11 ms apart; each
    client kept the gap only to itself."""
    shared = {"host_lock": asyncio.Lock(), "pacing": webif.Pacing()}
    first = connect(session, pump.host, gap=GAP, **shared)
    second = connect(session, pump.host, gap=GAP, **shared)

    await asyncio.gather(first.page(PAGE, whole), second.page(PAGE, whole))

    gaps = [later - earlier for earlier, later in pairwise(sorted(pump.arrivals))]
    assert len(gaps) == 5
    assert min(gaps) >= GAP - CLOCK_TOLERANCE


async def test_a_request_waits_while_modbus_holds_the_controller(pump, session):
    host_lock = asyncio.Lock()
    client = connect(session, pump.host, host_lock=host_lock)

    async with host_lock:
        reading = asyncio.create_task(client.page(PAGE, whole))
        await asyncio.sleep(SHORT_TIMEOUT)
        assert pump.asked == []

    assert await reading == WHOLE
    assert pump.asked == [*LOGIN, ("GET", PAGE)]


async def test_pages_asked_for_at_once_are_served_one_after_the_other(pump, session):
    """Two refreshes at once share one login instead of racing to two."""
    client = connect(session, pump.host)

    assert await asyncio.gather(client.page(PAGE, whole), client.page(PAGE, whole)) == [
        WHOLE,
        WHOLE,
    ]
    assert pump.asked == [*LOGIN, ("GET", PAGE), ("GET", PAGE)]
