"""Finding the pages to poll by their titles in the menus, once at setup.

A page's address is made of the controller's own codes, so it is read from
the menus rather than written down here. Three menu pages are asked for: the
overview, Info, and the heat pump's main menu. Nothing else is opened on the
way; the heat pump menu also lists Reset.
"""

from collections import Counter
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


class DoubledMenuEntries(Broken):
    """A menu showed an entry the pages are found by twice, also when asked again.

    `titles` are the doubled ones: which of two links leads to the page cannot
    be told. Now and then a hiccup, which asking again mends; on a model that
    shows the menu so, never.
    """

    def __init__(self, titles: set[str]) -> None:
        """Name the doubled entries."""
        super().__init__(f"menus with {', '.join(sorted(titles))} twice")
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
    read: Callable[[str], list[tuple[str, str]]],
) -> dict[str, str]:
    """The entries the page at path shows, title to link, `wanted` among them."""
    shown: list[tuple[str, str]] = []

    def complete(text: str) -> bool:
        shown[:] = read(text)
        counts = Counter(title for title, _ in shown)
        # A wanted title shown twice leaves two links to pick from.
        return all(counts[title] == 1 for title in wanted)

    try:
        await client.page(path, complete)
    except Broken as error:
        counts = Counter(title for title, _ in shown)
        missing = wanted - set(counts)
        # A menu showing nothing of its own level came half: asking again a
        # little later may mend it. One showing other entries is in another
        # language or of another model.
        if not shown:
            raise
        if missing:
            raise MissingMenuEntries(missing) from error
        doubled = {title for title in wanted if counts[title] > 1}
        raise DoubledMenuEntries(doubled) from error
    return dict(shown)


def _links_below(path: str) -> Callable[[str], list[tuple[str, str]]]:
    """The entries one level below the menu at path, title and link."""

    def links(text: str) -> list[tuple[str, str]]:
        return [
            (entry.title, entry.href)
            for entry in pages.children(text, path)
            if entry.href is not None
        ]

    return links
