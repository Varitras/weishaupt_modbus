"""How the client talks to the web interface, against a stand-in on loopback.

The stand-in answers the way the controller did in the recordings: a login
is a 303 to /home.html with a session cookie, wrong credentials a 303 to
/index.html#wrongpassword without one, and a page asked for without a valid
session a 303 to /index.html.
"""

import asyncio
from itertools import pairwise
import socket
import time

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from custom_components.weishaupt_modbus.webif import client as webif

PAGE_PATH = "/settings_export.html"
PAGE = (
    PAGE_PATH
    + "?stack=64000001000000000080000F4C010002000301,64001800000000000080000F4C020003000401"
)
WHOLE = "<p>whole</p>"
BROKEN = "<p>broken</p>"
USER = "tester"
PASSWORD = "testing"
LOGIN = [("GET", webif.INDEX), ("POST", webif.LOGIN)]
GAP = 0.2
# The event loop may wake a sleeper a whisker early by its clock resolution.
CLOCK_TOLERANCE = 0.001
SHORT_TIMEOUT = 0.1
SLOW = 0.5


def whole(text):
    return text == WHOLE


def see_other(location):
    return web.Response(status=303, headers={"Location": location})


class StandInPump:
    """Answers like the controller, and notes what it was asked."""

    def __init__(self):
        self.host = ""
        self.asked = []
        self.arrivals = []
        self.forms = []
        self.pages = [WHOLE]
        self.status = 200
        self.delays = {}
        self.keeps_sessions = True
        self.sessions = set()

    def application(self):
        @web.middleware
        async def note(request, handler):
            self.asked.append((request.method, request.raw_path))
            self.arrivals.append(time.monotonic())
            await asyncio.sleep(self.delays.get(request.path, 0))
            return await handler(request)

        application = web.Application(middlewares=[note])
        application.router.add_get(webif.INDEX, self.index)
        application.router.add_post(webif.LOGIN, self.login)
        application.router.add_get(webif.LOGOUT, self.logout)
        application.router.add_get(PAGE_PATH, self.page)
        return application

    async def index(self, request):
        return web.Response(text="<form class='form-signin'></form>")

    async def login(self, request):
        form = dict(await request.post())
        self.forms.append(form)
        if form.get("pass") != PASSWORD:
            return see_other("/index.html#wrongpassword")
        session = str(len(self.forms))
        if self.keeps_sessions:
            self.sessions.add(session)
        answer = see_other(webif.LOGIN_TARGET)
        answer.set_cookie(webif.SESSION_COOKIE, session)
        return answer

    async def logout(self, request):
        self.sessions.discard(request.cookies.get(webif.SESSION_COOKIE))
        return see_other("/index.html#loggedout")

    async def page(self, request):
        if request.cookies.get(webif.SESSION_COOKIE) not in self.sessions:
            return see_other(webif.INDEX)
        text = self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        return web.Response(status=self.status, text=text)


@pytest.fixture
async def pump(socket_enabled):
    """A stand-in pump on the loopback address."""
    stand_in = StandInPump()
    # A request the client gave up on must not outlive the test.
    server = TestServer(
        stand_in.application(), host="127.0.0.1", handler_cancellation=True
    )
    await server.start_server()
    stand_in.host = f"127.0.0.1:{server.port}"
    yield stand_in
    await server.close()


@pytest.fixture
async def session():
    async with aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(unsafe=True)
    ) as session:
        yield session


def connect(session, host, password=PASSWORD, gap=0, **options):
    """A client without the gap between requests, unless a test asks for one."""
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


async def test_wrong_credentials_end_the_round_at_the_login(pump, session):
    client = connect(session, pump.host, password="wrong")

    with pytest.raises(webif.LoginRefused):
        await client.page(PAGE, whole)
    assert pump.asked == LOGIN


async def test_a_session_cookie_the_jar_drops_is_no_login(pump):
    """aiohttp's default jar drops a cookie from an IP address; every page
    would come back as a redirect to the login."""
    async with aiohttp.ClientSession() as default_jar:
        client = connect(default_jar, pump.host)

        with pytest.raises(webif.LoginRefused):
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
    pump.delays[PAGE_PATH] = SLOW
    client = connect(session, pump.host, timeout=SHORT_TIMEOUT)

    with pytest.raises(webif.Unreachable):
        await client.page(PAGE, whole)
    assert pump.asked == [*LOGIN, ("GET", PAGE)]


async def test_a_pump_that_takes_no_connection_is_unreachable(socket_enabled, session):
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
    client = connect(session, f"127.0.0.1:{port}")

    with pytest.raises(webif.Unreachable):
        await client.page(PAGE, whole)


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
    client = connect(session, pump.host)
    await client.close()
    assert pump.asked == []

    await client.page(PAGE, whole)
    pump.asked.clear()
    await client.close()
    await client.close()
    assert pump.asked == [("GET", webif.LOGOUT)]


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
        PAGE_PATH,
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


async def test_pages_asked_for_at_once_are_served_one_after_the_other(pump, session):
    """Two refreshes at once share one login instead of racing to two."""
    client = connect(session, pump.host)

    assert await asyncio.gather(client.page(PAGE, whole), client.page(PAGE, whole)) == [
        WHOLE,
        WHOLE,
    ]
    assert pump.asked == [*LOGIN, ("GET", PAGE), ("GET", PAGE)]
