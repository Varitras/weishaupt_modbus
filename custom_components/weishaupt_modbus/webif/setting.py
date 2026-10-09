"""Writing the heating power limit: read its form fresh, save it once, read back.

When to write, how often and against which daily limit is the caller's. The
entry is found by its title on the heating page, never by a stored address:
the controller writes the value into the entry's address, so every save
moves it.
"""

from collections.abc import Callable

from . import pages
from .client import SETTING_LIST, Broken, Client, Unreachable, WebifError

POWER_LIMIT_TITLE = "Leistungsbegrenzung"
PERCENT = "%"


class FormNotRead(WebifError):
    """The heating page or the limit's own page did not come whole; nothing was sent."""


class FormMismatch(WebifError):
    """The form is not the one of the value shown, or lacks the target; nothing was sent."""


class OtherValueShown(WebifError):
    """Saved, and the heating page shows another value than the one sent."""

    def __init__(self, text: str, shown: int, target: int) -> None:
        """Keep the page read back: it is what the controller holds now."""
        super().__init__(f"{POWER_LIMIT_TITLE}: {shown} shown, {target} sent")
        self.text = text
        self.shown = shown
        self.target = target


class NotReadBack(WebifError):
    """Saved, and the heating page did not come whole afterwards."""


def shown_percent(shown: str) -> int | None:
    """The whole percentage a value shows, 60 for "60 %"; None for anything else."""
    number = pages.number(shown)
    if pages.unit(shown) != PERCENT or number is None or not number.is_integer():
        return None
    return int(number)


def _limit_entry(page: str, heating_path: str) -> tuple[int, str] | None:
    """The value the power limit shows and its link; None unless shown once."""
    found = [
        (shown, entry.href)
        for entry in pages.children(page, heating_path)
        if entry.title == POWER_LIMIT_TITLE
        and entry.href is not None
        and (shown := shown_percent(entry.text)) is not None
    ]
    return found[0] if len(found) == 1 else None


async def _read[T](
    client: Client, path: str, parse: Callable[[str], T | None]
) -> tuple[str, T]:
    """The page at path and what `parse` made of it; whole means parsed."""
    parsed: list[T] = []

    def complete(text: str) -> bool:
        result = parse(text)
        if result is None:
            return False
        parsed.append(result)
        return True

    text = await client.page(path, complete)
    # The client hands back the last text `complete` accepted.
    return text, parsed[-1]


async def write_power_limit(
    client: Client,
    heating_path: str,
    whole: Callable[[str], bool],
    target: int,
    count_write: Callable[[], None],
) -> str:
    """Set the power limit to `target`; the heating page as read afterwards.

    `whole` is the heating page's own completeness rule. `count_write` runs
    right before the save goes out: answered or not, it may reach the EEPROM.
    A target the controller already shows ends the write after the first read.

    Raises FormNotRead or FormMismatch with nothing sent, NotSaved or
    MaybeSaved from the save, OtherValueShown or NotReadBack after it.
    """

    def limit_shown(text: str) -> tuple[int, str] | None:
        return _limit_entry(text, heating_path) if whole(text) else None

    try:
        heating, (shown, link) = await _read(client, heating_path, limit_shown)
        if shown == target:
            return heating
        _, form = await _read(client, link, pages.setting_form)
    except (Broken, Unreachable) as error:
        raise FormNotRead(str(error)) from error
    link_stack = link.split(pages.STACK_QUERY, 1)[-1]
    mismatched = (
        form.stack != link_stack
        or form.selected != shown
        or form.type != SETTING_LIST
        or target not in form.offered
    )
    if mismatched:
        raise FormMismatch(
            f"{POWER_LIMIT_TITLE}: the form is not the one of {shown} or lacks {target}"
        )
    count_write()
    await client.save(form, target)
    try:
        read_back, (now_shown, _) = await _read(client, heating_path, limit_shown)
    except (Broken, Unreachable) as error:
        raise NotReadBack(str(error)) from error
    if now_shown != target:
        raise OtherValueShown(read_back, now_shown, target)
    return read_back
