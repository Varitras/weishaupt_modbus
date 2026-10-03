"""What a page of the web interface says, and whether it said all of it.

Pure functions over the HTML text; fetching is the client's business. Every
entry on a page is a "nav-link browseobj": a value is a <div>, a menu entry
an <a> that may show its child's current value after the title.

The controller answers a slow internal read with a complete 200 page that is
nevertheless wrong: an empty menu column, the main menu or the parent's
siblings nested one level too deep, an entry without its value, or the
values of another section. Nothing in the status says so; only the content
does, which is why every check here looks at the content.
"""

from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
import re

ENTRY_CLASS = "browseobj"
ENTRY_TAGS = ("a", "div")
# The one page the controller serves its menus and values from; a stack of
# menu codes in the query names which.
PAGE_PATH = "/settings_export.html"
STACK_QUERY = "stack="
STACK_LINK = re.compile(
    re.escape(PAGE_PATH) + r"\?" + STACK_QUERY + r"[0-9A-F]{38}(?:,[0-9A-F]{38})*"
)
# A stack segment names its own menu depth in these two hex digits (01 for a
# main menu, 02 below it, ...). The broken answers nest entries one level too
# deep while they keep their old depth.
SEGMENT_DEPTH = slice(26, 28)
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
# The controller writes "Aus" for an idle power, request or pump speed.
OFF = "Aus"
NO_VALUE = "--"


@dataclass(frozen=True)
class Entry:
    """One entry of a page: its title, the text after it, and its link if it is one."""

    title: str
    text: str
    href: str | None


class _Entries(HTMLParser):
    """Every browseobj entry in page order."""

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[Entry] = []
        self._depth = 0
        self._tag = ""
        self._href: str | None = None
        self._title = ""
        self._text = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if self._depth == 0:
            self._open(tag, attributes)
            return
        if tag == self._tag:
            self._depth += 1
        elif tag == "h5":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if self._depth == 0:
            return
        if tag == "h5":
            self._in_title = False
        elif tag == self._tag:
            self._depth -= 1
            if self._depth == 0:
                self.entries.append(
                    Entry(_normalised(self._title), _normalised(self._text), self._href)
                )

    def handle_data(self, data: str) -> None:
        if self._depth == 0:
            return
        if self._in_title:
            self._title += data
        else:
            self._text += data

    def _open(self, tag: str, attributes: dict[str, str | None]) -> None:
        classes = (attributes.get("class") or "").split()
        if tag not in ENTRY_TAGS or ENTRY_CLASS not in classes:
            return
        self._depth = 1
        self._tag = tag
        self._href = attributes.get("href") if tag == "a" else None
        self._title = ""
        self._text = ""
        self._in_title = False


def _normalised(text: str) -> str:
    return " ".join(text.split())


def entries(page: str) -> list[Entry]:
    """Every entry of the page, values and menu links alike, in page order."""
    parser = _Entries()
    parser.feed(page)
    return parser.entries


def values(page: str) -> list[tuple[str, str]]:
    """Title and text of every value on the page, in page order.

    A list, not a mapping: the fault memory repeats its titles.
    """
    return [(entry.title, entry.text) for entry in entries(page) if entry.href is None]


def main_menus(page: str) -> dict[str, str]:
    """The main menus the page lists, title to link.

    A main menu's link is one stack segment; the broken answers that nest the
    main menus under another entry give them two.
    """
    return {
        entry.title: entry.href
        for entry in entries(page)
        if entry.href is not None
        and STACK_LINK.fullmatch(entry.href) is not None
        and "," not in entry.href
    }


def is_child(href: str, parent: str) -> bool:
    """One level below parent, and the link's own segment says so."""
    if STACK_LINK.fullmatch(href) is None or not href.startswith(parent + ","):
        return False
    segments = href.split(STACK_QUERY, 1)[1].split(",")
    return len(segments) == parent.count(",") + 2 and int(
        segments[-1][SEGMENT_DEPTH], 16
    ) == len(segments)


def children(page: str, parent: str) -> list[Entry]:
    """The menu entries one level below parent, with the value each one shows."""
    return [
        entry
        for entry in entries(page)
        if entry.href is not None and is_child(entry.href, parent)
    ]


def is_complete(found: list[tuple[str, str]], required: frozenset[str]) -> bool:
    """Every required title is there once, and no value is left blank.

    The required titles are what tells a heat pump page from the statistics
    the controller sometimes sends in its place. One shown twice would leave
    its sensor two values to pick from.
    """
    counts = Counter(title for title, _ in found)
    shown_once = all(counts[title] == 1 for title in required)
    return bool(found) and shown_once and all(text for _, text in found)


def unit(text: str) -> str:
    """What follows the leading number of a shown value: "BAR" in "24.4 BAR"."""
    match = NUMBER.match(text)
    return text[match.end() :].strip() if match else ""


def number(text: str) -> float | None:
    """The leading number of a shown value; None for "--" or text."""
    match = NUMBER.match(text)
    return float(match.group(0)) if match else None
