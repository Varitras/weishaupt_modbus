"""Finding the pages to poll by their titles in the menus.

Once at setup, and again while polling when the controller has given its
menus new codes and an address holding the old ones brings the overview.

A page's address is made of the controller's own codes, so it is read from
the menus rather than written down here. Three menu pages are asked for: the
overview, Info, and the heat pump's main menu. Nothing else is opened on the
way to them; the heat pump menu also lists Reset. The titles are German; a
controller set to English shows its own, which stand for them.

An Evoblock's heat pump menu also lists Modulation, the heat pump's output
while the hot water loads, whose entry leads straight to its form: taken
when shown, as the pages below are.

Info also lists a page per heating circuit and one for the second heat
generator. They are taken when Info shows them, and left out otherwise: a
pump without them, or set to English, whose titles for them nobody has seen,
is polled as before.

The settings of each circuit Info lists and of the hot water sit one menu
further down from the overview: Heizkreis n › Einstellungen, Warmwasser ›
Einstellungen. They are searched apart, once the caller knows the model has
values on them, so another model is asked only what it was before: their
menus, which list Reset as well, are the only others asked for.
"""

from collections import Counter
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
import re

from . import pages
from .client import OVERVIEW, Broken, Client
from .setting import MODULATION_TITLE

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
MODULATION_ENGLISH = "modulation"
STATISTICS_ENGLISH = "statistics"
HEATING_ENGLISH = "heating"
ENGLISH_MENU_TITLES = {
    INFO_ENGLISH: INFO,
    HEAT_PUMP_ENGLISH: HEAT_PUMP,
    STATISTICS_ENGLISH: STATISTICS,
    HEATING_ENGLISH: HEATING,
    MODULATION_ENGLISH: MODULATION_TITLE,
}

HEAT_PUMP_PAGE = "heat_pump"
STATISTICS_PAGE = "statistics"
HEATING_PAGE = "heating"
HEATING_CIRCUIT_PAGE = "heating_circuit"
SECOND_GENERATOR_PAGE = "second_generator"
HOT_WATER_SETTINGS_PAGE = "hot_water_settings"
MODULATION_PAGE = "modulation"


def circuit_settings_page(circuit: int) -> str:
    """The key of a heating circuit's settings page."""
    return f"circuit_settings_{circuit}"


def circuit_menu(circuit: int) -> str:
    """The title of a heating circuit's main menu."""
    return f"Heizkreis {circuit}"


CIRCUIT_SETTINGS_PAGES = tuple(circuit_settings_page(n) for n in CIRCUITS)
# The pages only some pumps show.
OPTIONAL_PAGES = (
    HEATING_CIRCUIT_PAGE,
    SECOND_GENERATOR_PAGE,
    *CIRCUIT_SETTINGS_PAGES,
    HOT_WATER_SETTINGS_PAGE,
    MODULATION_PAGE,
)
# The pages that are a setting's own form: its generic fields cannot tell
# which setting owns an address the controller gave to another.
FORM_PAGES = (MODULATION_PAGE,)
# Where each page sits in the controller's menus, as its user finds it there
# in German and in English; the optional pages were seen in German only.
MENU_PATHS: dict[str, tuple[str, str | None]] = {
    HEAT_PUMP_PAGE: (f"{INFO} › {HEAT_PUMP}", f"{INFO_ENGLISH} › {HEAT_PUMP_ENGLISH}"),
    STATISTICS_PAGE: (
        f"{INFO} › {STATISTICS}",
        f"{INFO_ENGLISH} › {STATISTICS_ENGLISH}",
    ),
    HEATING_PAGE: (
        f"{HEAT_PUMP} › {HEATING}",
        f"{HEAT_PUMP_ENGLISH} › {HEATING_ENGLISH}",
    ),
    HEATING_CIRCUIT_PAGE: (f"{INFO} › Heizkreis", None),
    SECOND_GENERATOR_PAGE: (f"{INFO} › {SECOND_GENERATOR}", None),
    **{
        circuit_settings_page(n): (f"{circuit_menu(n)} › {SETTINGS}", None)
        for n in CIRCUITS
    },
    HOT_WATER_SETTINGS_PAGE: (f"{HOT_WATER} › {SETTINGS}", None),
    MODULATION_PAGE: (
        f"{HEAT_PUMP} › {MODULATION_TITLE}",
        f"{HEAT_PUMP_ENGLISH} › {MODULATION_ENGLISH}",
    ),
}


def in_both_languages(german: str, english: str | None) -> str:
    """A name in German, and in English after it where one was seen."""
    return german if english is None else f"{german} ({english})"


PAGE_MENUS = {key: in_both_languages(*paths) for key, paths in MENU_PATHS.items()}


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

    `titles` are the doubled ones, or the ones sharing their link with
    another title: which of two links, or which of two pages, is the one
    asked for cannot be told. Now and then a hiccup, which asking again mends; on a model that
    shows the menu so, never.
    """

    def __init__(self, titles: set[str]) -> None:
        """Name the doubled entries."""
        super().__init__(f"menus with {', '.join(sorted(titles))} twice")
        self.titles = titles


@dataclass(frozen=True)
class Menus:
    """The entries each menu asked for showed once, title to link."""

    main: dict[str, str]
    info: dict[str, str]
    heat_pump: dict[str, str]


def moved(text: str, path: str) -> bool:
    """The overview came in place of the page at path: its codes are old.

    The controller gave its menus new codes once (2026-11-17, an Evoblock),
    and an address of the old ones then brought the overview, whole and with
    HTTP 200: the main menus alone, linked under the new codes. A page served
    half shows the main menus too, sometimes nothing beside them, but under
    the codes its address holds; only those tell the two apart.
    """
    menus = _main_menus(text)
    if INFO not in menus or HEAT_PUMP not in menus:
        return False
    first = path.partition(pages.STACK_QUERY)[2].split(",", 1)[0]
    # Every main menu shown, also one under a link another title shares: the
    # menus read clearly may lack the one with the address's code.
    return all(
        link.partition(pages.STACK_QUERY)[2] != first
        for _, link in pages.main_menus(text)
    )


def leads_from(text: str, path: str, title: str) -> bool:
    """The page at path sits under the main menu `title`, as the page shows it.

    Every page lists the main menus; the one linked by the address's first
    segment is the page's own. The settings pages of two circuits show the
    same rows: when the controller gave a retired circuit's address to
    another one, only this told them apart. As discovery takes a menu, the
    title must be shown once, and no other title at that link.
    """
    shown = Counter(
        ENGLISH_MENU_TITLES.get(each, each) for each, _ in pages.main_menus(text)
    )
    return shown[title] == 1 and menu_owners(text, path) == [title]


def menu_owners(text: str, path: str) -> list[str]:
    """The main menus the page shows under its address's first segment."""
    first = path.partition(pages.STACK_QUERY)[2].split(",", 1)[0]
    return [
        ENGLISH_MENU_TITLES.get(shown, shown)
        for shown, link in pages.main_menus(text)
        if link.partition(pages.STACK_QUERY)[2] == first
    ]


def _main_menus(text: str) -> dict[str, str]:
    """The main menus the page shows clearly, German title to link."""
    return _clear(
        [
            (ENGLISH_MENU_TITLES.get(title, title), link)
            for title, link in pages.main_menus(text)
        ]
    )


def _clear(shown: list[tuple[str, str]]) -> dict[str, str]:
    """The entries shown once, under a link no other title shares, title to link.

    A title shown twice leaves two links to pick from; a link two titles
    share may lead to either page, the cooling one for Heizen, another
    circuit's settings for a circuit.
    """
    titles = Counter(title for title, _ in shown)
    links = Counter(link for _, link in shown)
    return {
        title: link for title, link in shown if titles[title] == 1 and links[link] == 1
    }


def shared_addresses(paths: Mapping[str, str]) -> set[str]:
    """The keys of the pages whose address another page has too."""
    counts = Counter(paths.values())
    return {key for key, path in paths.items() if counts[path] > 1}


async def find_pages(
    client: Client,
    overview: str | None = None,
    *,
    reads: Callable[[dict[str, str]], Awaitable[Collection[str]]] | None = None,
    last: bool = True,
) -> dict[str, str]:
    """The address of each page to poll, by page key; the setup's and a round's.

    `overview` is an answer listing the main menus under their current codes:
    the overview `moved` took, or another circuit's page at the address. The
    main menus are read from it rather than asked for again. `reads` gets the
    other pages found and gives the keys of those its model has values on,
    reading them first if it must; only the settings pages among those are
    searched, as `find_settings_pages` does with `last`. Without it, none are.

    Raises WebifError when a menu will not show what it must.
    """
    menus = await find_menus(client, overview)
    found = pages_in(menus)
    if reads is None:
        return found
    keys = await reads(found)
    if wanted := {
        key: menu for key, menu in settings_menus(menus).items() if key in keys
    }:
        found |= await find_settings_pages(client, wanted, last=last)
    return found


async def find_menus(client: Client, overview: str | None = None) -> Menus:
    """The overview, Info and the heat pump menu, as they show their entries.

    `overview` is as for `find_pages`. Raises WebifError when one of them
    will not show what it must.
    """
    if overview is None:
        main = await _entries(client, OVERVIEW, {INFO, HEAT_PUMP}, pages.main_menus)
    else:
        main = _main_menus(overview)
        if missing := {INFO, HEAT_PUMP} - set(main):
            raise MissingMenuEntries(missing)
    info = await _entries(
        client, main[INFO], {HEAT_PUMP, STATISTICS}, _links_below(main[INFO])
    )
    heat_pump = await _entries(
        client, main[HEAT_PUMP], {HEATING}, _links_below(main[HEAT_PUMP])
    )
    return Menus(main, info, heat_pump)


def pages_in(menus: Menus) -> dict[str, str]:
    """The address of each page the menus link to, by page key."""
    return {
        HEAT_PUMP_PAGE: menus.info[HEAT_PUMP],
        STATISTICS_PAGE: menus.info[STATISTICS],
        HEATING_PAGE: menus.heat_pump[HEATING],
        **_optional_pages(menus.info),
        **_modulation(menus.heat_pump),
    }


def settings_menus(menus: Menus) -> dict[str, str]:
    """The menu each settings page would be found in, by page key.

    A circuit's only when Info lists it and the overview shows its menu.
    """
    wanted = {
        circuit_settings_page(number): circuit_menu(number)
        for number, _ in _circuits(menus.info)
        if number in CIRCUITS
    }
    wanted[HOT_WATER_SETTINGS_PAGE] = HOT_WATER
    return {key: menus.main[menu] for key, menu in wanted.items() if menu in menus.main}


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


def _modulation(heat_pump: dict[str, str]) -> dict[str, str]:
    """The modulation's form among the heat pump menu's entries."""
    return (
        {MODULATION_PAGE: heat_pump[MODULATION_TITLE]}
        if MODULATION_TITLE in heat_pump
        else {}
    )


async def find_settings_pages(
    client: Client, menus: Mapping[str, str], *, last: bool
) -> dict[str, str]:
    """The settings page in each of `menus`, by page key.

    A menu without its settings entry leaves its page out. One served half
    or showing the entry twice raises, for a second search, unless this is
    the `last`: then it leaves its page out too.
    """
    found: dict[str, str] = {}
    for key, menu in menus.items():
        try:
            entries = await _entries(client, menu, {SETTINGS}, _links_below(menu))
        except MissingMenuEntries:
            continue
        except Broken:
            if not last:
                raise
            continue
        found[key] = entries[SETTINGS]
    return found


async def _entries(
    client: Client,
    path: str,
    wanted: set[str],
    read: Callable[[str], list[tuple[str, str]]],
) -> dict[str, str]:
    """The entries the page at path shows clearly, title to link, `wanted` among them.

    An entry shown unclearly, twice or under a link another title shares, is
    left out; a wanted one fails the menu.
    """
    shown: list[tuple[str, str]] = []

    def complete(text: str) -> bool:
        shown[:] = [
            (ENGLISH_MENU_TITLES.get(title, title), link) for title, link in read(text)
        ]
        return wanted <= set(_clear(shown))

    try:
        await client.page(path, complete)
    except Broken as error:
        missing = wanted - {title for title, _ in shown}
        # A menu showing nothing of its own level came half: asking again a
        # little later may mend it. One showing other entries is in another
        # language or of another model.
        if not shown:
            raise
        if missing:
            raise MissingMenuEntries(missing) from error
        raise DoubledMenuEntries(wanted - set(_clear(shown))) from error
    return _clear(shown)


def _links_below(path: str) -> Callable[[str], list[tuple[str, str]]]:
    """The entries one level below the menu at path, title and link."""

    def links(text: str) -> list[tuple[str, str]]:
        return [
            (entry.title, entry.href)
            for entry in pages.children(text, path)
            if entry.href is not None
        ]

    return links
