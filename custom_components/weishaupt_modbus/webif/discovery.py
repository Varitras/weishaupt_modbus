"""Finding the pages to poll by their titles in the menus, once at setup.

A page's address is made of the controller's own codes, so it is read from
the menus rather than written down here. Three menu pages are asked for: the
overview, Info, and the heat pump's main menu. Nothing else is opened on the
way; the heat pump menu also lists Reset.
"""

from . import pages
from .client import OVERVIEW, Client

INFO = "Info"
HEAT_PUMP = "Wärmepumpe"
STATISTICS = "Statistik"
HEATING = "Heizen"

HEAT_PUMP_PAGE = "heat_pump"
STATISTICS_PAGE = "statistics"
HEATING_PAGE = "heating"


async def find_pages(client: Client) -> dict[str, str]:
    """The address of each page to poll, by page key.

    Raises WebifError when a menu will not show what it must.
    """
    overview = await client.page(
        OVERVIEW, lambda text: {INFO, HEAT_PUMP} <= pages.main_menus(text).keys()
    )
    menus = pages.main_menus(overview)
    info = await _menu(client, menus[INFO], {HEAT_PUMP, STATISTICS})
    heat_pump = await _menu(client, menus[HEAT_PUMP], {HEATING})
    return {
        HEAT_PUMP_PAGE: info[HEAT_PUMP],
        STATISTICS_PAGE: info[STATISTICS],
        HEATING_PAGE: heat_pump[HEATING],
    }


async def _menu(client: Client, path: str, wanted: set[str]) -> dict[str, str]:
    """The entries one level below the menu at path, title to link."""

    def links(text: str) -> dict[str, str]:
        return {
            entry.title: entry.href
            for entry in pages.children(text, path)
            if entry.href is not None
        }

    return links(await client.page(path, lambda text: wanted <= links(text).keys()))
