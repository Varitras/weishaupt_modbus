"""Finding the pages to poll by their titles in the menus, once at setup.

A page's address is made of the controller's own codes, so it is read from
the menus rather than written down here. Three menu pages are asked for: the
overview, Info, and the heat pump's main menu. Nothing else is opened on the
way; the heat pump menu also lists Reset. The titles are German; a
controller set to English shows its own, which stand for them.

Info also lists a page per heating circuit and one for the second heat
generator. They are taken when Info shows them, and left out otherwise: a
pump without them, or set to English, whose titles for them nobody has seen,
is polled as before. So are the settings of each circuit Info lists and of
the hot water, one menu further down from the overview: Heizkreis n ›
Einstellungen, Warmwasser › Einstellungen. Their menus, which list Reset as
well, are the only others asked for.
"""

from collections import Counter
from collections.abc import Callable
import re

from . import pages
from .client import OVERVIEW, Broken, Client

INFO = "Info"
HEAT_PUMP = "Wärmepumpe"
STATISTICS = "Statistik"
HEATING = "Heizen"
# Info lists "Heizkreis 2", "Heizkreis 3", ... with the circuit's own name
# after the title; an Evoblock showed no Heizkreis 1 there (2026-11-17), its
# first circuit being the heat pump itself.
HEATING_CIRCUIT = re.compile(r"Heizkreis (\d)")
SECOND_GENERATOR = "2. WEZ"
HOT_WATER = "Warmwasser"
SETTINGS = "Einstellungen"
# The circuits a controller numbers, 1 to 5 as over Modbus.
CIRCUITS = range(1, 6)
# As a controller set to English showed them, 2026-10-10.
INFO_ENGLISH = "information"
HEAT_PUMP_ENGLISH = "heat pump"
STATISTICS_ENGLISH = "statistics"
HEATING_ENGLISH = "heating"
ENGLISH_MENU_TITLES = {
    INFO_ENGLISH: INFO,
    HEAT_PUMP_ENGLISH: HEAT_PUMP,
    STATISTICS_ENGLISH: STATISTICS,
    HEATING_ENGLISH: HEATING,
}

HEAT_PUMP_PAGE = "heat_pump"
STATISTICS_PAGE = "statistics"
HEATING_PAGE = "heating"
HEATING_CIRCUIT_PAGE = "heating_circuit"
SECOND_GENERATOR_PAGE = "second_generator"
HOT_WATER_SETTINGS_PAGE = "hot_water_settings"


def circuit_settings_page(circuit: int) -> str:
    """The key of a heating circuit's settings page."""
    return f"circuit_settings_{circuit}"


CIRCUIT_SETTINGS_PAGES = tuple(circuit_settings_page(n) for n in CIRCUITS)
# The pages only some pumps show.
OPTIONAL_PAGES = (
    HEATING_CIRCUIT_PAGE,
    SECOND_GENERATOR_PAGE,
    *CIRCUIT_SETTINGS_PAGES,
    HOT_WATER_SETTINGS_PAGE,
)
# Where each page sits in the controller's menus, as its user finds it there
# in German and in English. The optional pages were seen in German only.
PAGE_MENUS = {
    HEAT_PUMP_PAGE: f"{INFO} › {HEAT_PUMP} ({INFO_ENGLISH} › {HEAT_PUMP_ENGLISH})",
    STATISTICS_PAGE: f"{INFO} › {STATISTICS} ({INFO_ENGLISH} › {STATISTICS_ENGLISH})",
    HEATING_PAGE: f"{HEAT_PUMP} › {HEATING} ({HEAT_PUMP_ENGLISH} › {HEATING_ENGLISH})",
    HEATING_CIRCUIT_PAGE: f"{INFO} › Heizkreis",
    SECOND_GENERATOR_PAGE: f"{INFO} › {SECOND_GENERATOR}",
    **{circuit_settings_page(n): f"Heizkreis {n} › {SETTINGS}" for n in CIRCUITS},
    HOT_WATER_SETTINGS_PAGE: f"{HOT_WATER} › {SETTINGS}",
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
        **_optional_pages(info),
        **await _settings_pages(client, menus, _circuits(info)),
    }


def _circuits(info: dict[str, str]) -> list[tuple[int, str]]:
    """The heating circuits Info lists, number and link, lowest first."""
    return sorted(
        (int(match.group(1)), link)
        for title, link in info.items()
        if (match := HEATING_CIRCUIT.fullmatch(title))
    )


def _optional_pages(info: dict[str, str]) -> dict[str, str]:
    """The optional pages among Info's entries; the first heating circuit's.

    The outdoor temperature averages a circuit's page shows are the same on
    every circuit, so one is read.
    """
    found: dict[str, str] = {}
    circuits = _circuits(info)
    if circuits:
        found[HEATING_CIRCUIT_PAGE] = circuits[0][1]
    if SECOND_GENERATOR in info:
        found[SECOND_GENERATOR_PAGE] = info[SECOND_GENERATOR]
    return found


async def _settings_pages(
    client: Client, menus: dict[str, str], circuits: list[tuple[int, str]]
) -> dict[str, str]:
    """The settings pages of the circuits Info lists and of the hot water.

    A menu without its settings entry, or with two, leaves its page out.
    """
    wanted = {
        circuit_settings_page(number): f"Heizkreis {number}"
        for number, _ in circuits
        if number in CIRCUITS
    }
    wanted[HOT_WATER_SETTINGS_PAGE] = HOT_WATER
    found: dict[str, str] = {}
    for key, menu in wanted.items():
        if menu not in menus:
            continue
        try:
            entries = await _entries(
                client, menus[menu], {SETTINGS}, _links_below(menus[menu])
            )
        except MissingMenuEntries, DoubledMenuEntries:
            continue
        found[key] = entries[SETTINGS]
    return found


async def _entries(
    client: Client,
    path: str,
    wanted: set[str],
    read: Callable[[str], list[tuple[str, str]]],
) -> dict[str, str]:
    """The entries the page at path shows once, title to link, `wanted` among them.

    An entry shown twice leaves two links to pick from; it is left out.
    """
    shown: list[tuple[str, str]] = []

    def complete(text: str) -> bool:
        shown[:] = [
            (ENGLISH_MENU_TITLES.get(title, title), link) for title, link in read(text)
        ]
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
    counts = Counter(title for title, _ in shown)
    return {title: link for title, link in shown if counts[title] == 1}


def _links_below(path: str) -> Callable[[str], list[tuple[str, str]]]:
    """The entries one level below the menu at path, title and link."""

    def links(text: str) -> list[tuple[str, str]]:
        return [
            (entry.title, entry.href)
            for entry in pages.children(text, path)
            if entry.href is not None
        ]

    return links
