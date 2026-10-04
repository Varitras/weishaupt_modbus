"""The short visit a dialog pays the web interface.

It logs in, finds the pages to poll by their titles, reads each once and logs
out: a login the controller refuses, or a page another model shows, fails in
the dialog rather than in the brake minutes after the entry was added.
"""

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .configentry import host_lock, host_pacing
from .webif.client import Broken, Client, WebifError
from .webif.discovery import PAGE_MENUS, DoubledMenuEntries, find_pages
from .webif_coordinator import Page, polled_pages
from .webif_sensor import REQUIRED_TITLES, shown_in_unknown_units


class MissingTitles(Broken):
    """A page that came without titles its sensors read.

    Another model or firmware than the one this was built for, not a hiccup.
    """

    def __init__(self, page: str, titles: frozenset[str]) -> None:
        """Name the page by its menus, and the titles it lacks."""
        super().__init__(f"{page}: {', '.join(sorted(titles))}")
        self.page = page
        self.titles = titles


class UnclearValues(Broken):
    """A page that showed all its titles, some without a value or twice.

    Now and then a hiccup, which asking again mends; on a model that shows
    the page so, never.
    """

    def __init__(self, page: str, titles: frozenset[str]) -> None:
        """Name the page by its menus, and the titles shown so."""
        super().__init__(f"{page}: {', '.join(sorted(titles))}")
        self.page = page
        self.titles = titles


class UnknownUnits(WebifError):
    """A whole page showing values in a unit their sensors do not read."""

    def __init__(self, page: str, titles: frozenset[str]) -> None:
        """Name the page by its menus, and the titles in another unit."""
        super().__init__(f"{page}: {', '.join(sorted(titles))}")
        self.page = page
        self.titles = titles


async def read_web_interface(
    hass: HomeAssistant, host: str, user: str, password: str
) -> dict[str, str]:
    """The pages to poll, found and read once on a short visit that logs out.

    Pages served half are searched once more in the same session before the
    visit gives up. Raises WebifError.
    """
    # No entry to detach it on unload: the visit detaches it itself.
    session = async_create_clientsession(
        hass, auto_cleanup=False, cookie_jar=aiohttp.DummyCookieJar()
    )
    client = Client(
        session,
        host,
        user,
        password,
        host_lock=host_lock(hass, host),
        pacing=host_pacing(hass, host),
    )
    try:
        return await _visit_twice(client)
    finally:
        # The dialog may close while the logout runs; the session goes anyway.
        # Closed during the login POST, the visit never sees the new session's
        # cookie and cannot log it out. Accepted: the controller drops such a
        # session on its own.
        try:
            await client.close()
        finally:
            session.detach()


# What a second search may mend: a page served half, values or a menu entry
# shown unclearly. Listed rather than excluded, so a kind of failure added
# later costs the struggling controller no second search by default.
SEARCHED_AGAIN = (Broken, UnclearValues, DoubledMenuEntries)


async def _visit_twice(client: Client) -> dict[str, str]:
    """The visit, once more after a page served half or unclear.

    The controller now and then serves a page half, twice in a row. Titles
    or menu entries a page lacks are another model's or language's, and a
    second search would only add load before saying so; so would one after
    any failure not named in SEARCHED_AGAIN.
    """
    try:
        return await _visit(client)
    except Broken as error:
        if type(error) not in SEARCHED_AGAIN:
            raise
    return await _visit(client)


async def _visit(client: Client) -> dict[str, str]:
    """The pages found, each read once.

    A page another model shows without a title its sensors read fails here,
    not in the brake ten minutes after the entry was added.
    """
    found = await find_pages(client)
    for page in polled_pages(found, {}, REQUIRED_TITLES):
        await _read_whole(client, page)
    return found


async def _read_whole(client: Client, page: Page) -> None:
    shown: list[str] = []

    def whole(text: str) -> bool:
        shown.append(text)
        return page.is_whole(text)

    try:
        text = await client.page(page.path, whole)
    except Broken as error:
        lacking = _lacking(page, shown[-1] if shown else "")
        if lacking is None:
            raise
        raise lacking from error
    if unknown := shown_in_unknown_units(page.key, page.read(text)):
        raise UnknownUnits(PAGE_MENUS[page.key], unknown)


def _lacking(page: Page, text: str) -> Broken | None:
    """What keeps a page that came from being whole.

    None for a page that came only half: a page of values with none of its
    titles (the controller's empty column, its main menus nested, another
    section's values), or a menu with nothing at its own level. Asking again
    a little later mends that. A menu with other entries at its own level is
    another model's, as in the search.
    """
    titles = {title for title, _ in page.read(text)}
    shows_its_own_level = page.menu and bool(titles)
    if not titles & page.required and not shows_its_own_level:
        return None
    if missing := page.missing(text):
        return MissingTitles(PAGE_MENUS[page.key], missing)
    return UnclearValues(PAGE_MENUS[page.key], page.unclear(text))
