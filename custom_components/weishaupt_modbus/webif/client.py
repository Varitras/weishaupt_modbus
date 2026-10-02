"""Talking to the web interface: one request at a time, paced, never pushed.

What the pump gets from us is the point of this module. At most one request
is in flight, a gap lies between any two, each has a hard time limit, a page
that arrived whole but wrong gets exactly one more try, and after a timeout
or a broken connection nothing follows in this round. Only the addresses in
the positive list are ever asked for: the access code form on every page
saves by GET, so an address with any other query could change the pump.
"""

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from http import HTTPStatus
import time

import aiohttp

from . import pages

INDEX = "/index.html"
LOGIN = "/login.html"
LOGOUT = "/logout.html"
OVERVIEW = "/settings_export.html"
LOGIN_TARGET = "/home.html"
SESSION_COOKIE = "session"
# The longest whole answer in a 60-minute run took 15.5 s.
TIMEOUT_SECONDS = 20.0
MIN_GAP_SECONDS = 5.0
# Renewed daily: nothing is known of week-long sessions on this embedded server.
SESSION_LIFETIME_SECONDS = 24 * 3600.0


class WebifError(Exception):
    """The web interface did not give what was asked for."""


class Unreachable(WebifError):
    """No complete answer in time, or the connection broke: the server struggles."""


class LoginRefused(WebifError):
    """The credentials were not accepted."""


class Broken(WebifError):
    """The page came whole but wrong, also after its one more try."""


@dataclass(frozen=True)
class _Answer:
    status: int
    location: str
    text: str

    @property
    def session_lost(self) -> bool:
        """A page asked for without a valid session is redirected to the login.

        The controller does not answer with the login form itself.
        """
        return self.status == HTTPStatus.SEE_OTHER and self.location == INDEX


def _allowed(method: str, path: str) -> bool:
    if method == "POST":
        return path == LOGIN
    return (
        path in (INDEX, LOGOUT, OVERVIEW)
        or pages.STACK_LINK.fullmatch(path) is not None
    )


def _page_text(path: str, answer: _Answer) -> str:
    if answer.status != HTTPStatus.OK:
        raise Broken(f"{path}: HTTP {answer.status}")
    return answer.text


class Client:
    """One login, one page at a time, for one pump.

    The session must keep cookies of an IP address (aiohttp's default jar
    drops them): aiohttp.CookieJar(unsafe=True).
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        user: str,
        password: str,
        *,
        host_lock: asyncio.Lock,
        gap: float | None = None,
        timeout: float = TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Prepare the client; nothing is sent before the first page.

        host_lock is the one Modbus takes for the same controller.
        """
        self._session = session
        self._base = f"http://{host}"
        self._credentials = {"user": user, "pass": password}
        self._host_lock = host_lock
        # Read here, not as the default, so a test can shorten the gap for
        # the clients that the dialogs and the setup create.
        self._gap = MIN_GAP_SECONDS if gap is None else gap
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._clock = clock
        self._lock = asyncio.Lock()
        self._last_request: float | None = None
        self._logged_in_at: float | None = None
        self._closed = False

    async def page(self, path: str, complete: Callable[[str], bool]) -> str:
        """The page at path, whole by `complete`.

        A lost session is renewed once; a page that came whole but wrong is
        asked for once more. Raises WebifError otherwise.
        """
        async with self._lock:
            if self._closed:
                raise WebifError(f"{path}: the client is closed")
            await self._ensure_session()
            answer = await self._request("GET", path)
            if answer.session_lost:
                self._logged_in_at = None
                await self._login()
                answer = await self._request("GET", path)
            text = _page_text(path, answer)
            if complete(text):
                return text
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
        cookies = {cookie.key for cookie in self._session.cookie_jar}
        accepted = (
            answer.status == HTTPStatus.SEE_OTHER
            and answer.location == LOGIN_TARGET
            and SESSION_COOKIE in cookies
        )
        if not accepted:
            raise LoginRefused(f"HTTP {answer.status} to {answer.location or '-'}")
        # A deep page answers with a redirect to the login until the session
        # has opened the menu overview (live test, 2026-10-02).
        _page_text(OVERVIEW, await self._request("GET", OVERVIEW))
        self._logged_in_at = self._clock()

    async def _request(
        self, method: str, path: str, form: dict[str, str] | None = None
    ) -> _Answer:
        if not _allowed(method, path):
            raise ValueError(f"{method} {path} is not on the positive list")
        await self._pace()
        async with self._host_lock:
            try:
                async with self._session.request(
                    method,
                    self._base + path,
                    data=form,
                    allow_redirects=False,
                    timeout=self._timeout,
                ) as response:
                    body = await response.read()
                    location = response.headers.get("Location", "")
                    return _Answer(
                        response.status,
                        location,
                        body.decode("utf-8", errors="replace"),
                    )
            except (TimeoutError, aiohttp.ClientError) as error:
                # The kind only: aiohttp's own text names the pump's address.
                raise Unreachable(f"{method} {path}: {type(error).__name__}") from error
            finally:
                self._last_request = self._clock()

    async def _pace(self) -> None:
        if self._last_request is None:
            return
        wait = self._last_request + self._gap - self._clock()
        if wait > 0:
            await asyncio.sleep(wait)
