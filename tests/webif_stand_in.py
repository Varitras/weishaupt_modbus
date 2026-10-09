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
# Where the controller saves a setting's form.
SAVE_PATH = "/pro_save.html"
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
HEATING_PATH = STACK + f"{PUMP_MENU},{HEATING}"
SWITCHING_DIFFERENCE = "64001805000000002D40000A0B030011010401"
LIMIT_OPTIONS = range(10, 101)


def limit_segment(limit):
    """The power limit's own segment: the controller writes its value into it."""
    return f"6400180700000000{limit:02X}40000A0B030011010401"


def heating_page(limit, switching="4.5 K"):
    """Wärmepumpe › Heizen with the power limit and the switching difference."""
    return MAIN_MENUS + column(
        link([PUMP_MENU, HEATING, SWITCHING_DIFFERENCE], "Schaltdifferenz", switching)
        + link(
            [PUMP_MENU, HEATING, limit_segment(limit)],
            "Leistungsbegrenzung",
            f"{limit} %",
        )
    )


def limit_leaf(limit, offered=LIMIT_OPTIONS):
    """The power limit's own page: the one form that saves it."""
    options = "".join(
        f'<option value="{option}"{" selected" if option == limit else ""}>\n'
        f"{option}</option>\n"
        for option in offered
    )
    return MAIN_MENUS + (
        '<form action="pro_save.html" method="POST">'
        f'<input type="hidden" name="id" value="{limit_segment(limit)}">\n'
        '<input type="hidden" name="stack" '
        f'value="{PUMP_MENU},{HEATING},{limit_segment(limit)}">\n'
        '<input type="hidden" name="type" value="para_list">'
        '<div class="form-group"><select class="form-control" name="value">\n'
        f"{options}</select></div>\n"
        '<button type="submit" class="btn btn-success">Speichern</button></form>\n'
    )


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
    answer to every login instead. A path in `failing` is answered with its
    error status alone, one in `failing_once` so the next time it is asked. A
    save is noted in `saved` (its form) and `save_headers` (its Referer and
    Origin); `save_answer`, when set, answers it instead. A page in `stalls` sends its headers and its first
    character at once, and the rest after that many seconds; one in
    `trickles` sends one character every that many seconds. With
    `watched_lock` set, `held` notes for every request whether that lock was
    held when it arrived.
    """

    def __init__(self):
        self.host = ""
        self.asked = []
        self.arrivals = []
        self.forms = []
        self.site = {}
        self.pages = [WHOLE]
        self.status = 200
        self.failing = {}
        self.failing_once = {}
        self.delays = {}
        self.stalls = {}
        self.trickles = {}
        self.watched_lock = None
        self.held = []
        self.keeps_sessions = True
        self.sets_cookie = True
        self.login_answer = None
        self.sessions = set()
        self.saved = []
        self.save_headers = []
        self.save_answer = None

    def application(self):
        @web.middleware
        async def note(request, handler):
            self.asked.append((request.method, request.raw_path))
            self.arrivals.append(time.monotonic())
            if self.watched_lock is not None:
                self.held.append(self.watched_lock.locked())
            await asyncio.sleep(self.delays.get(request.raw_path, 0))
            if request.raw_path in self.failing:
                return web.Response(status=self.failing[request.raw_path])
            if request.raw_path in self.failing_once:
                return web.Response(status=self.failing_once.pop(request.raw_path))
            return await handler(request)

        application = web.Application(middlewares=[note])
        application.router.add_get(webif.INDEX, self.index)
        application.router.add_post(webif.LOGIN, self.login)
        application.router.add_get(webif.LOGOUT, self.logout)
        application.router.add_get(PAGE_PATH, self.page)
        application.router.add_post(SAVE_PATH, self.save)
        return application

    async def index(self, request):
        return web.Response(text="<form class='form-signin'></form>")

    async def login(self, request):
        form = dict(await request.post())
        self.forms.append(form)
        if self.login_answer is not None:
            return self.login_answer()
        if form.get(webif.LOGIN_PASSWORD_FIELD) != PASSWORD:
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

    def show_power_limit(self, limit):
        """The heating page and the power limit's own page, for a limit."""
        for path in [path for path in self.site if path.startswith(HEATING_PATH + ",")]:
            del self.site[path]
        self.site[HEATING_PATH] = heating_page(limit)
        self.site[f"{HEATING_PATH},{limit_segment(limit)}"] = limit_leaf(limit)

    async def save(self, request):
        """Set the posted value as the controller does: the links move with it.

        What the controller does with a form that is no longer current is not
        known; here it is answered like a save and changes nothing.
        """
        form = dict(await request.post())
        self.saved.append(form)
        self.save_headers.append(
            {name: request.headers.get(name) for name in ("Referer", "Origin")}
        )
        if self.save_answer is not None:
            return self.save_answer()
        if session_cookie(request) not in self.sessions:
            return see_other(webif.INDEX)
        current = STACK + form.get("stack", "") in self.site
        if current and form.get("value", "").isdigit():
            self.show_power_limit(int(form["value"]))
        return see_other(HEATING_PATH)

    async def page(self, request):
        if session_cookie(request) not in self.sessions:
            return see_other(webif.INDEX)
        text = self.site.get(request.raw_path)
        if text is None:
            text = self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        if request.raw_path in self.trickles:
            return await self._trickled(request, text)
        if request.raw_path not in self.stalls:
            return web.Response(status=self.status, text=text)
        response = web.StreamResponse(status=self.status)
        await response.prepare(request)
        await response.write(text[:1].encode())
        await asyncio.sleep(self.stalls[request.raw_path])
        await response.write(text[1:].encode())
        return response

    async def _trickled(self, request, text):
        response = web.StreamResponse(status=self.status)
        await response.prepare(request)
        for character in text:
            await response.write(character.encode())
            await asyncio.sleep(self.trickles[request.raw_path])
        return response


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
