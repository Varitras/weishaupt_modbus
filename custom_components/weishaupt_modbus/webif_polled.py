"""The pages an entry polls: where each is, what it must show, how often.

How a page is read for its values - a page of values, a menu showing its
entries' values, or a setting's own form - and when it counts as whole.
When the pages are asked for is the coordinator's.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from .const import CONST
from .webif import pages
from .webif.discovery import (
    CIRCUIT_SETTINGS_PAGES,
    CIRCUITS,
    HEAT_PUMP_PAGE,
    HEATING_CIRCUIT_PAGE,
    HEATING_PAGE,
    HOT_WATER_SETTINGS_PAGE,
    MODULATION_PAGE,
    OPTIONAL_PAGES,
    SECOND_GENERATOR_PAGE,
    STATISTICS_PAGE,
    circuit_menu,
    circuit_settings_page,
    leads_from,
    menu_owners,
)
from .webif.setting import MODULATION_TITLE, own_form

SETTINGS_PAGES = (*CIRCUIT_SETTINGS_PAGES, HOT_WATER_SETTINGS_PAGE)
# A wrong page in place of the asked one lacks every title: more than these
# are counted, not listed, in the notice and the log.
TITLES_NAMED = 5
# Page key: the option holding its interval, and the default in minutes.
INTERVAL_OPTIONS = {
    HEAT_PUMP_PAGE: (
        CONST.OPTION_WEBIF_HEAT_PUMP_INTERVAL,
        CONST.WEBIF_INTERVAL_MINUTES,
    ),
    STATISTICS_PAGE: (
        CONST.OPTION_WEBIF_STATISTICS_INTERVAL,
        CONST.WEBIF_SLOW_INTERVAL_MINUTES,
    ),
    HEATING_PAGE: (
        CONST.OPTION_WEBIF_HEATING_INTERVAL,
        CONST.WEBIF_SLOW_INTERVAL_MINUTES,
    ),
    HEATING_CIRCUIT_PAGE: (
        CONST.OPTION_WEBIF_HEATING_CIRCUIT_INTERVAL,
        CONST.WEBIF_SLOW_INTERVAL_MINUTES,
    ),
    SECOND_GENERATOR_PAGE: (
        CONST.OPTION_WEBIF_SECOND_GENERATOR_INTERVAL,
        CONST.WEBIF_SLOW_INTERVAL_MINUTES,
    ),
    # The settings pages share one: they change only when someone sets them.
    **dict.fromkeys(
        SETTINGS_PAGES,
        (CONST.OPTION_WEBIF_SETTINGS_INTERVAL, CONST.WEBIF_SLOW_INTERVAL_MINUTES),
    ),
}

# The main menu each circuit's settings page must sit under: their rows are
# the same on every circuit.
CIRCUIT_OWNERS = {circuit_settings_page(n): circuit_menu(n) for n in CIRCUITS}

# The modulation changes only when someone sets it, and a write reads it
# back at once: an hour spares the controller a page every round.
MODULATION_INTERVAL = timedelta(hours=1)


@dataclass(frozen=True)
class Page:
    """A page to keep fresh: where it is, what it must show, how often.

    A menu page is read for the values its entries show one level down; a
    form page, a setting's own, for the option its form has selected, under
    the title `form`.
    `english` maps the titles a controller shows otherwise, set to English or
    spelt another way, to the German ones everything else goes by. `optional` are titles read when the
    page shows them, which it need not. `owner` is the main menu the page must
    show itself under, where its titles alone cannot tell it from another's.
    A page that is not `strict` is judged by the rows it reads alone.
    """

    key: str
    path: str
    required: frozenset[str]
    interval: timedelta
    menu: bool = False
    form: str | None = None
    # Left out of the hash: a dict has none, and every page lost its own.
    english: Mapping[str, str] = field(default_factory=dict, hash=False)
    optional: frozenset[str] = frozenset()
    owner: str | None = None
    strict: bool = True

    def read(self, text: str) -> list[tuple[str, str]]:
        """The titles and values the page shows, English titles made German."""
        if self.form is not None:
            found = self._selected(text, self.form)
        elif self.menu:
            found = [
                (entry.title, entry.text) for entry in pages.children(text, self.path)
            ]
        else:
            found = pages.values(text)
        return [(self.english.get(title, title), shown) for title, shown in found]

    def _selected(self, text: str, title: str) -> list[tuple[str, str]]:
        form = own_form(text, self.path)
        return [] if form is None else [(title, str(form.selected))]

    def is_whole(self, text: str) -> bool:
        """Every required title once, none read twice, none blank, under its owner."""
        complete = pages.is_complete(self._judged(text), self.required, self.optional)
        return complete and self.owned(text)

    def _judged(self, text: str) -> list[tuple[str, str]]:
        """The rows its completeness goes by."""
        found = self.read(text)
        if self.strict:
            return found
        read = self.required | self.optional
        return [(title, shown) for title, shown in found if title in read]

    def owned(self, text: str) -> bool:
        """Under its owner's main menu, or without one to be under."""
        return self.owner is None or leads_from(text, self.path, self.owner)

    def under_another(self, text: str) -> bool:
        """Shown under another main menu than its owner's.

        Another circuit's page at its address. A page served half shows no
        main menu, or still its owner's under the address's code.
        """
        owners = menu_owners(text, self.path)
        return self.owner is not None and bool(owners) and self.owner not in owners

    def missing(self, text: str) -> frozenset[str]:
        """The required titles the page does not show."""
        return self.required - {title for title, _ in self.read(text)}

    def unclear(self, text: str) -> frozenset[str]:
        """The titles shown without a value, and the ones read shown twice."""
        found = self._judged(text)
        counts = Counter(title for title, _ in found)
        empty = {title for title, shown in found if not shown}
        twice = {title for title in self.required | self.optional if counts[title] > 1}
        return frozenset(empty | twice)

    def flaws(self, text: str) -> str:
        """What keeps the page from being whole, by its titles, never a value."""
        named = (
            ("missing", self.missing(text)),
            ("blank or doubled", self.unclear(text)),
        )
        flaws = [f"{kind}: {_titles(titles)}" for kind, titles in named if titles]
        if self.under_another(text):
            flaws.append(f"not under {self.owner}")
        return "; ".join(flaws)


def _titles(titles: frozenset[str]) -> str:
    if len(titles) > TITLES_NAMED:
        return f"{len(titles)} titles"
    return ", ".join(sorted(title or "untitled" for title in titles))


def polled_pages(
    paths: Mapping[str, str],
    options: Mapping[str, Any],
    required: Mapping[str, frozenset[str]],
    english: Mapping[str, Mapping[str, str]],
    optional: Mapping[str, frozenset[str]],
) -> list[Page]:
    """The pages at `paths`, each on the interval its option sets.

    `required` are the titles each page must show; `english` maps each page's
    English titles to them; `optional` are the titles its sensors read that a
    page may leave out. An optional page is polled only when it was found and
    its family has sensors on it.
    """

    def asked_for(key: str) -> bool:
        # User decision, 2026-11-20: a page more for a value seldom changed.
        return key != MODULATION_PAGE or options.get(
            CONST.OPTION_WEBIF_MODULATION, False
        )

    def every(key: str) -> timedelta:
        if key == MODULATION_PAGE:
            return MODULATION_INTERVAL
        option, default = INTERVAL_OPTIONS[key]
        return timedelta(minutes=options.get(option, default))

    return [
        Page(
            HEAT_PUMP_PAGE,
            paths[HEAT_PUMP_PAGE],
            required[HEAT_PUMP_PAGE],
            every(HEAT_PUMP_PAGE),
            english=english[HEAT_PUMP_PAGE],
            optional=optional[HEAT_PUMP_PAGE],
        ),
        Page(
            STATISTICS_PAGE,
            paths[STATISTICS_PAGE],
            required[STATISTICS_PAGE],
            every(STATISTICS_PAGE),
            english=english[STATISTICS_PAGE],
            optional=optional[STATISTICS_PAGE],
        ),
        Page(
            HEATING_PAGE,
            paths[HEATING_PAGE],
            required[HEATING_PAGE],
            every(HEATING_PAGE),
            menu=True,
            english=english[HEATING_PAGE],
            optional=optional[HEATING_PAGE],
        ),
        *(
            Page(
                key,
                paths[key],
                required[key],
                every(key),
                # A settings page shows its values one level down, as entries.
                menu=key in SETTINGS_PAGES,
                form=MODULATION_TITLE if key == MODULATION_PAGE else None,
                english=english.get(key, {}),
                optional=optional.get(key, frozenset()),
                owner=CIRCUIT_OWNERS.get(key),
                # A settings page also lists rows no sensor reads, such as a
                # circuit's name: one left blank must not fail the page.
                strict=key not in SETTINGS_PAGES,
            )
            for key in OPTIONAL_PAGES
            if key in paths and required.get(key) and asked_for(key)
        ),
    ]
