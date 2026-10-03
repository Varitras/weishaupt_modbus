"""A stand-in for the pump's web interface on the loopback address.

It answers the way the controller did in the recordings: a login is a 303 to
/home.html with a session cookie, wrong credentials a 303 to
/index.html#wrongpassword without one, and a page asked for without a valid
session a 303 to /index.html. A session id counts only as sent: the
controller does not unquote a quoted one.

The menus are synthetic, shaped like the controller's. A recorded page
carries a serial number, an access code and addresses and never enters the
repository; the stack segments here carry a made-up device token (0A0B).
"""

import asyncio
from contextlib import asynccontextmanager
import time

from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.weishaupt_modbus.webif import client as webif

USER = "tester"
PASSWORD = "testing"
WHOLE = "<p>whole</p>"
PAGE_PATH = webif.OVERVIEW
STACK = PAGE_PATH + "?stack="
INFO = "0C000001000000000080000A0B010002000301"
PUMP_MENU = "64000001000000000080000A0B010002000301"
HEAT_PUMP_INFO = "0C000C22000000000000000A0B020003000401"
STATISTICS_INFO = "0C000C23000000000000000A0B020003000401"
HEATING = "64001800000000000080000A0B020003000401"
RESET = "64001900000000000080000A0B020003000401"
# The controller's ids are base64-like, 131 characters with + and /.
SESSION_ID = "Kq3/Zt+w" * 16


def see_other(location):
    return web.Response(status=303, headers={"Location": location})


def session_cookie(request):
    """The session id as the client sent it, quotes and all."""
    for pair in request.headers.get("Cookie", "").split(";"):
        name, _, value = pair.strip().partition("=")
        if name == webif.SESSION_COOKIE:
            return value
    return None


def link(segments, title, shown=""):
    href = STACK + ",".join(segments)
    return (
        f'<a class="nav-link browseobj" href="{href}" role="tab">\n'
        f"<h5>{title}</h5>\n{shown}\n</a>\n"
    )


def value(title, text):
    return (
        '<div class="nav-link browseobj" role="tab">\n'
        f"<h5>{title}</h5>\n{text}\n</div>\n"
    )


def column(inner):
    return (
        '<div class="col-3">\n<div class="nav flex-column nav-pills" role="tablist">'
        f"{inner}</div></div>\n"
    )


MAIN_MENUS = column(link([INFO], "Info") + link([PUMP_MENU], "Wärmepumpe"))


def menu_site():
    """The overview, Info and the heat pump menu, by the address asked for."""
    return {
        PAGE_PATH: MAIN_MENUS,
        STACK + INFO: MAIN_MENUS
        + column(
            link([INFO, HEAT_PUMP_INFO], "Wärmepumpe")
            + link([INFO, STATISTICS_INFO], "Statistik")
        ),
        STACK + PUMP_MENU: MAIN_MENUS
        + column(
            link([PUMP_MENU, HEATING], "Heizen") + link([PUMP_MENU, RESET], "Reset")
        ),
    }


class StandInPump:
    """Answers like the controller, and notes what it was asked.

    A page is answered from `site` by its address, otherwise from `pages`
    in turn, the last one for good. `login_answer`, when set, builds the
    answer to every login instead.
    """

    def __init__(self):
        self.host = ""
        self.asked = []
        self.arrivals = []
        self.forms = []
        self.site = {}
        self.pages = [WHOLE]
        self.status = 200
        self.delays = {}
        self.keeps_sessions = True
        self.sets_cookie = True
        self.login_answer = None
        self.sessions = set()

    def application(self):
        @web.middleware
        async def note(request, handler):
            self.asked.append((request.method, request.raw_path))
            self.arrivals.append(time.monotonic())
            await asyncio.sleep(self.delays.get(request.raw_path, 0))
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
        if self.login_answer is not None:
            return self.login_answer()
        if form.get("pass") != PASSWORD:
            return see_other("/index.html#wrongpassword")
        session = f"{len(self.forms)}+{SESSION_ID}"
        if self.keeps_sessions:
            self.sessions.add(session)
        answer = see_other(webif.LOGIN_TARGET)
        if self.sets_cookie:
            answer.headers["Set-Cookie"] = f"{webif.SESSION_COOKIE}={session}; path=/"
        return answer

    async def logout(self, request):
        self.sessions.discard(session_cookie(request))
        return see_other("/index.html#loggedout")

    async def page(self, request):
        if session_cookie(request) not in self.sessions:
            return see_other(webif.INDEX)
        text = self.site.get(request.raw_path)
        if text is None:
            text = self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        return web.Response(status=self.status, text=text)


@asynccontextmanager
async def serving(stand_in):
    """Serve the stand-in on the loopback address while the block runs."""
    # A request the client gave up on must not outlive the test.
    server = TestServer(
        stand_in.application(), host="127.0.0.1", handler_cancellation=True
    )
    await server.start_server()
    stand_in.host = f"127.0.0.1:{server.port}"
    try:
        yield stand_in
    finally:
        await server.close()
