"""Finding the pages to poll by their titles in the menus, once at setup.

A page's address is made of the controller's own codes, so it is read from
the menus rather than written down here. Three menu pages are asked for: the
overview, Info, and the heat pump's main menu. Nothing else is opened on the
way; the heat pump menu also lists Reset.
"""

from collections.abc import Callable

from . import pages
from .client import OVERVIEW, Broken, Client

INFO = "Info"
HEAT_PUMP = "Wärmepumpe"
STATISTICS = "Statistik"
HEATING = "Heizen"

HEAT_PUMP_PAGE = "heat_pump"
STATISTICS_PAGE = "statistics"
HEATING_PAGE = "heating"
# Where each page sits in the controller's menus, as its user finds it there.
PAGE_MENUS = {
    HEAT_PUMP_PAGE: f"{INFO} › {HEAT_PUMP}",
    STATISTICS_PAGE: f"{INFO} › {STATISTICS}",
    HEATING_PAGE: f"{HEAT_PUMP} › {HEATING}",
}


class MissingMenuEntries(Broken):
    """A menu came without entries the pages are found by, twice.

    `titles` are the missing ones. Set to another language, the controller
    shows them whole under other names, and asking again cannot help.
    """

    def __init__(self, titles: set[str]) -> None:
        """Name the missing entries."""
        super().__init__(f"menus without {', '.join(sorted(titles))}")
        self.titles = titles


async def find_pages(client: Client) -> dict[str, str]:
    """The address of each page to poll, by page key.

    Raises WebifError when a menu will not show what it must.
    """
    menus = await _entries(client, OVERVIEW, {INFO, HEAT_PUMP}, pages.main_menus)
    info = await _entries(
        client, menus[INFO], {HEAT_PUMP, STATISTICS}, _links_below(menus[INFO])
    )
    heat_pump = await _entries(
        client, menus[HEAT_PUMP], {HEATING}, _links_below(menus[HEAT_PUMP])
    )
    return {
        HEAT_PUMP_PAGE: info[HEAT_PUMP],
        STATISTICS_PAGE: info[STATISTICS],
        HEATING_PAGE: heat_pump[HEATING],
    }


async def _entries(
    client: Client,
    path: str,
    wanted: set[str],
    read: Callable[[str], dict[str, str]],
) -> dict[str, str]:
    """The entries the page at path shows, title to link, `wanted` among them."""
    shown: dict[str, str] = {}

    def complete(text: str) -> bool:
        shown.clear()
        shown.update(read(text))
        return wanted <= shown.keys()

    try:
        await client.page(path, complete)
    except Broken as error:
        raise MissingMenuEntries(wanted - shown.keys()) from error
    return shown


def _links_below(path: str) -> Callable[[str], dict[str, str]]:
    """The entries one level below the menu at path, title to link."""

    def links(text: str) -> dict[str, str]:
        return {
            entry.title: entry.href
            for entry in pages.children(text, path)
            if entry.href is not None
        }

    return links
