"""Talking to the web interface: one request at a time, paced, never pushed.

What the pump gets from us is the point of this module. At most one request
is in flight, a gap lies between any two, each has a hard time limit, a page
that arrived whole but wrong gets exactly one more try, and after a timeout
or a broken connection nothing follows in this round. Only the addresses in
the positive list are ever asked for: the access code form on every page
saves by GET, so an address with any other query could change the pump.
"""

import asyncio
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from http import HTTPStatus
import logging
import time

import aiohttp

from . import pages

_LOGGER = logging.getLogger(__name__)

INDEX = "/index.html"
LOGIN = "/login.html"
LOGOUT = "/logout.html"
OVERVIEW = "/settings_export.html"
LOGIN_TARGET = "/home.html"
WRONG_PASSWORD = INDEX + "#wrongpassword"
SESSION_COOKIE = "session"
# The longest whole answer in a 60-minute run took 15.5 s.
TIMEOUT_SECONDS = 20.0
MIN_GAP_SECONDS = 5.0
# Renewed daily: nothing is known of week-long sessions on this embedded server.
SESSION_LIFETIME_SECONDS = 24 * 3600.0


class WebifError(Exception):
    """The web interface did not give what was asked for."""


class Unreachable(WebifError):
    """The server struggles.

    No complete answer in time, a broken connection, an error status, a login
    it could not serve, or a session it dropped right after the login.
    """


class LoginRefused(WebifError):
    """The credentials were not accepted."""


class Broken(WebifError):
    """The page came whole but wrong, also after its one more try."""


class Closed(WebifError):
    """The client was closed: its entry unloads or Home Assistant stops."""


@dataclass
class Pacing:
    """The gap every web interface client of one controller keeps, together.

    A client takes the lock while it waits out the gap and asks; the lock
    Modbus shares is taken only for the request, so the wait never holds
    Modbus up.
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_request: float | None = None


@dataclass
class Traffic:
    """What a client asked of the controller, counted for its diagnostics.

    Every count starts at zero with the client; its sensor adds what it last
    recorded, so that a count runs on across restarts.
    """

    requests: int = 0
    logins: int = 0
    incomplete_pages: int = 0
    failed_reads: int = 0

    def restore(self, count: str, recorded: int) -> None:
        """A count as its sensor last recorded it, on top of this start's.

        Added, not assigned: the first round runs before the sensors restore,
        and what it asked is counted here already.
        """
        setattr(self, count, getattr(self, count) + recorded)


@dataclass(frozen=True)
class _Answer:
    status: int
    location: str
    text: str
    cookie: str | None

    @property
    def session_lost(self) -> bool:
        """A page asked for without a valid session is redirected to the login.

        The controller does not answer with the login form itself.
        """
        return self.status == HTTPStatus.SEE_OTHER and self.location == INDEX

    @property
    def wrong_password(self) -> bool:
        """The login form's answer to credentials it does not know."""
        return self.status == HTTPStatus.SEE_OTHER and self.location == WRONG_PASSWORD


def _allowed(method: str, path: str) -> bool:
    if method == "POST":
        return path == LOGIN
    return (
        path in (INDEX, LOGOUT, OVERVIEW)
        or pages.STACK_LINK.fullmatch(path) is not None
    )


def _session_cookie(headers: Iterable[str]) -> str | None:
    """The session id a Set-Cookie header carries, exactly as sent."""
    for header in headers:
        name, _, value = header.split(";", 1)[0].partition("=")
        if name.strip() == SESSION_COOKIE:
            return value.strip()
    return None


def _page_text(path: str, answer: _Answer) -> str:
    # Not a page served half: the server could not serve it, or dropped the
    # session it had just given, and asking further pages only adds load.
    if answer.status != HTTPStatus.OK:
        raise Unreachable(f"{path}: HTTP {answer.status} to {answer.location or '-'}")
    return answer.text


class Client:
    """One login, one page at a time, for one pump.

    The session must leave cookies alone (aiohttp.DummyCookieJar): the
    controller's session ids carry slashes, aiohttp's jar sends such an id
    back in quotes, and the controller then knows the session no more.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        user: str,
        password: str,
        *,
        host_lock: asyncio.Lock,
        pacing: Pacing,
        gap: float | None = None,
        timeout: float = TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Prepare the client; nothing is sent before the first page.

        host_lock is the one Modbus takes for the same controller, pacing
        the one every web interface client of it shares.
        """
        if not isinstance(session.cookie_jar, aiohttp.DummyCookieJar):
            raise TypeError("the session must leave cookies to the client")
        # aiohttp sends a GET once more by itself when the connection drops
        # before the answer: at once, outside the gap, to a controller that
        # has just struggled. There is no public switch; aiohttp's own test
        # client turns it off the same way.
        session._retry_connection = False
        self._session = session
        self._base = f"http://{host}"
        self._credentials = {"user": user, "pass": password}
        self._host_lock = host_lock
        self._pacing = pacing
        # Read here, not as the default, so a test can shorten the gap for
        # the clients that the dialogs and the setup create.
        self._gap = MIN_GAP_SECONDS if gap is None else gap
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._clock = clock
        self._lock = asyncio.Lock()
        self._logged_in_at: float | None = None
        self._cookie: str | None = None
        self._closed = False
        self.traffic = Traffic()
        self._slowest: float | None = None

    async def page(self, path: str, complete: Callable[[str], bool]) -> str:
        """The page at path, whole by `complete`.

        A lost session is renewed once; a page that came whole but wrong is
        asked for once more. Raises WebifError otherwise.
        """
        async with self._lock:
            if self._closed:
                raise Closed(f"{path}: the client is closed")
            try:
                return await self._read(path, complete)
            except Broken, Unreachable:
                self.traffic.failed_reads += 1
                raise

    def take_slowest_answer(self) -> float | None:
        """The longest answer since the last call, in seconds; None for none."""
        slowest, self._slowest = self._slowest, None
        return slowest

    async def _read(self, path: str, complete: Callable[[str], bool]) -> str:
        await self._ensure_session()
        answer = await self._request("GET", path)
        if answer.session_lost:
            self._logged_in_at = None
            await self._login()
            answer = await self._request("GET", path)
        text = _page_text(path, answer)
        if complete(text):
            return text
        self.traffic.incomplete_pages += 1
        text = _page_text(path, await self._request("GET", path))
        if not complete(text):
            raise Broken(path)
        return text

    async def close(self) -> None:
        """Log out, so the server frees the session at once.

        Nothing is asked after this: a round still running would log in
        again, and that session would stay open on the controller.
        """
        self._closed = True
        async with self._lock:
            # The server drops an abandoned session on its own; a logout that
            # fails must not fail the unload.
            with suppress(WebifError):
                await self._logout()

    async def _ensure_session(self) -> None:
        fresh = (
            self._logged_in_at is not None
            and self._clock() - self._logged_in_at < SESSION_LIFETIME_SECONDS
        )
        if fresh:
            return
        await self._logout()
        await self._login()

    async def _logout(self) -> None:
        if self._logged_in_at is None:
            return
        self._logged_in_at = None
        await self._request("GET", LOGOUT)

    async def _login(self) -> None:
        await self._request("GET", INDEX)
        answer = await self._request("POST", LOGIN, self._credentials)
        if answer.wrong_password:
            raise LoginRefused(f"HTTP {answer.status} to {answer.location}")
        accepted = (
            answer.status == HTTPStatus.SEE_OTHER
            and answer.location == LOGIN_TARGET
            and bool(answer.cookie)
        )
        if not accepted:
            # Its database out of reach (#nocon), an error status, no session:
            # the controller struggles, and the credentials may well be right.
            raise Unreachable(
                f"POST {LOGIN}: HTTP {answer.status} to {answer.location or '-'}"
            )
        self._cookie = answer.cookie
        self._logged_in_at = self._clock()
        self.traffic.logins += 1

    async def _request(
        self, method: str, path: str, form: dict[str, str] | None = None
    ) -> _Answer:
        if not _allowed(method, path):
            raise ValueError(f"{method} {path} is not on the positive list")
        async with self._pacing.lock:
            await self._pace()
            answer, seconds = await self._exchange(method, path, form)
        redirect = f" to {answer.location}" if answer.location else ""
        _LOGGER.debug(
            "%s %s: HTTP %s%s, %.1f s",
            method,
            path,
            answer.status,
            redirect,
            seconds,
        )
        return answer

    async def _exchange(
        self, method: str, path: str, form: dict[str, str] | None
    ) -> tuple[_Answer, float]:
        """One request under the lock Modbus shares, and how long it took."""
        cookie = {"Cookie": f"{SESSION_COOKIE}={self._cookie}"} if self._cookie else {}
        async with self._host_lock:
            started = self._clock()
            try:
                async with self._session.request(
                    method,
                    self._base + path,
                    data=form,
                    headers=cookie,
                    allow_redirects=False,
                    timeout=self._timeout,
                ) as response:
                    body = await response.read()
                    answer = _Answer(
                        response.status,
                        response.headers.get("Location", ""),
                        body.decode("utf-8", errors="replace"),
                        _session_cookie(response.headers.getall("Set-Cookie", [])),
                    )
            except (TimeoutError, aiohttp.ClientError) as error:
                # The kind only: aiohttp's own text names the pump's address.
                raise Unreachable(f"{method} {path}: {type(error).__name__}") from error
            finally:
                finished = self._clock()
                self._pacing.last_request = finished
                self.traffic.requests += 1
                self._slowest = max(finished - started, self._slowest or 0.0)
        return answer, finished - started

    async def _pace(self) -> None:
        last = self._pacing.last_request
        if last is None:
            return
        wait = last + self._gap - self._clock()
        if wait > 0:
            await asyncio.sleep(wait)
