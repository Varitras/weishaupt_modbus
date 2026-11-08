"""The short visit a dialog pays the web interface.

It logs in, finds the pages to poll by their titles, reads each once and logs
out: a login the controller refuses, or a page another model shows, fails in
the dialog rather than in the brake minutes after the entry was added.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .configentry import host_lock, host_pacing
from .webif.client import Broken, Client, WebifError
from .webif.discovery import (
    HEAT_PUMP_PAGE,
    HEATING_PAGE,
    PAGE_MENUS,
    STATISTICS_PAGE,
    DoubledMenuEntries,
    find_pages,
)
from .webif_coordinator import Page, polled_pages
from .webif_sensor import (
    MODELS,
    OPTIONAL_TITLES,
    REQUIRED_TITLES,
    TITLES_SHOWN_OTHERWISE,
    model_shown,
    shown_in_unknown_units,
)


@dataclass(frozen=True)
class Visited:
    """What a visit found: the pages to poll, and the family showing them."""

    pages: dict[str, str]
    model: str


class MissingTitles(Broken):
    """A page that came without titles its sensors need.

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
) -> Visited:
    """The pages to poll and their family, read once on a visit that logs out.

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


async def _visit_twice(client: Client) -> Visited:
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


async def _visit(client: Client) -> Visited:
    """The pages found, each read once, and the family they are of.

    A page another model shows without a title its sensors need fails here,
    not in the brake ten minutes after the entry was added.
    """
    found = await find_pages(client)
    polled = {
        model: {
            page.key: page
            for page in polled_pages(
                found,
                {},
                REQUIRED_TITLES[model],
                TITLES_SHOWN_OTHERWISE[model],
                OPTIONAL_TITLES[model],
            )
        }
        for model in MODELS
    }
    # An answer served half may lack the row that tells the family, so each
    # answer is judged by what it says itself.
    model = model_shown(await _read_whole(client, polled, HEAT_PUMP_PAGE, model_shown))
    for key in (STATISTICS_PAGE, HEATING_PAGE):
        await _read_whole(client, polled, key, lambda _: model)
    return Visited(found, model)


async def _read_whole(
    client: Client,
    polled: Mapping[str, Mapping[str, Page]],
    key: str,
    model_of: Callable[[str], str],
) -> str:
    """The page at key, whole by the rows of the family `model_of` tells."""
    shown: list[str] = [""]

    def page_of(text: str) -> Page:
        return polled[model_of(text)][key]

    def whole(text: str) -> bool:
        shown.append(text)
        return page_of(text).is_whole(text)

    try:
        text = await client.page(page_of("").path, whole)
    except Broken as error:
        lacking = _lacking(page_of(shown[-1]), shown[-1])
        if lacking is None:
            raise
        raise lacking from error
    page = page_of(text)
    if unknown := shown_in_unknown_units(key, page.read(text), model_of(text)):
        raise UnknownUnits(PAGE_MENUS[key], unknown)
    return text


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
