"""Writing a setting: read its form fresh, save it once, read back.

When to write, how often and against which daily limit is the caller's. The
entry is found by its title on its menu page, never by a stored address:
the controller writes the value into the entry's address, so every save
moves it. Three settings are written: the heating power limit, each
circuit's minimum flow temperature and an Evoblock's modulation.

The modulation's entry leads straight to its form, with no page of values
before it: that form is the page polled, found by its title in the heat
pump menu, and the one read before and after its save.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from . import pages
from .client import SETTING_LIST, Broken, Client, Unreachable, WebifError

POWER_LIMIT_TITLE = "Leistungsbegrenzung"
# Its title on a controller set to English, as shown there 2026-10-10.
POWER_LIMIT_ENGLISH_TITLE = "output limit"
PERCENT = "%"
# Heizkreis n › Einstellungen; no controller set to English has shown it.
MINIMUM_TEMPERATURE_TITLE = "Minimaltemperatur"
CELSIUS = "°C"
# Wärmepumpe › Modulation; its form offers -1 for Automatik.
MODULATION_TITLE = "Modulation"
AUTOMATIC = -1
AUTOMATIC_SHOWN = "Automatik"


class FormNotRead(WebifError):
    """The menu page or the setting's own page did not come whole; nothing was sent."""


class FormMismatch(WebifError):
    """The form is not the one of the value shown, or lacks the target; nothing was sent."""


class OtherValueShown(WebifError):
    """Saved, and the menu page shows another value than the one sent."""

    def __init__(self, text: str, shown: str, target: str, title: str) -> None:
        """Keep the page read back: it is what the controller holds now.

        `shown` and `target` are the values as the page shows them.
        """
        super().__init__(f"{title}: {shown} shown, {target} sent")
        self.text = text
        self.shown_text = shown
        self.target_text = target


class NotReadBack(WebifError):
    """Saved, and the menu page did not come whole afterwards."""


def shown_percent(shown: str) -> int | None:
    """The whole percentage a value shows, 60 for "60 %"; None for anything else."""
    number = pages.number(shown)
    if pages.unit(shown) != PERCENT or number is None or not number.is_integer():
        return None
    return int(number)


def shown_tenths(shown: str) -> int | None:
    """A temperature in tenths of a degree, 335 for "33.5 °C"; None for anything else.

    The form offers a temperature so: "335" for 33.5.
    """
    number = pages.number(shown)
    if pages.unit(shown) != CELSIUS or number is None:
        return None
    tenths = round(number * 10)
    return tenths if abs(number * 10 - tenths) < 1e-6 else None


def shown_option(shown: str) -> int | None:
    """The form's option a form page's reading is, -1 for "-1"; None for anything else."""
    return int(shown) if pages.is_option(shown) else None


@dataclass(frozen=True)
class Setting:
    """A setting written: its titles, and how its values map to the form's options.

    `option` is the option of the form a shown value is; `shown` each option
    as the page shows it. `form_page` marks a setting whose page polled is
    its own form.
    """

    titles: tuple[str, ...]
    option: Callable[[str], int | None]
    shown: Callable[[int], str]
    form_page: bool = False

    @property
    def title(self) -> str:
        """The German title, which errors name."""
        return self.titles[0]

    @property
    def english_title(self) -> str | None:
        """The title on a controller set to English; None where never seen."""
        return self.titles[1] if len(self.titles) > 1 else None


POWER_LIMIT = Setting(
    (POWER_LIMIT_TITLE, POWER_LIMIT_ENGLISH_TITLE),
    shown_percent,
    lambda option: f"{option} {PERCENT}",
)
MINIMUM_TEMPERATURE = Setting(
    (MINIMUM_TEMPERATURE_TITLE,),
    shown_tenths,
    lambda option: f"{option / 10:.1f} {CELSIUS}",
)
MODULATION = Setting(
    (MODULATION_TITLE,),
    shown_option,
    lambda option: AUTOMATIC_SHOWN if option == AUTOMATIC else f"{option} {PERCENT}",
    form_page=True,
)


def _entry(page: str, menu_path: str, setting: Setting) -> tuple[int, str] | None:
    """The option the setting shows and its link; None unless shown once."""
    found = [
        (shown, entry.href)
        for entry in pages.children(page, menu_path)
        if entry.title in setting.titles
        and entry.href is not None
        and (shown := setting.option(entry.text)) is not None
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


async def write_setting(
    client: Client,
    menu_path: str,
    whole: Callable[[str], bool],
    setting: Setting,
    target: int,
    count_write: Callable[[], object],
) -> str:
    """Set `setting` to the option `target`; its menu page as read afterwards.

    `whole` is the menu page's own completeness rule. For a setting on a
    form page, `menu_path` is that page, and the form tells it whole.
    `count_write` runs as the save goes out, see Client.save. A target the
    controller already shows ends the write after the first read.

    Raises FormNotRead or FormMismatch with nothing sent, LoginRefused or
    Closed from the reads before the save, NotSaved, MaybeSaved or Closed
    from the save, OtherValueShown or NotReadBack after it.
    """
    if setting.form_page:
        return await _write_form(client, menu_path, setting, target, count_write)

    def setting_shown(text: str) -> tuple[int, str] | None:
        return _entry(text, menu_path, setting) if whole(text) else None

    try:
        menu, (shown, link) = await _read(client, menu_path, setting_shown)
        if shown == target:
            return menu
        _, form = await _read(client, link, pages.setting_form)
    except (Broken, Unreachable) as error:
        raise FormNotRead(str(error)) from error
    link_stack = link.split(pages.STACK_QUERY, 1)[-1]
    # Accepted: the form's id goes unchecked. Recorded on one controller only,
    # it is the leaf segment with digits changed, not the same ones on every
    # page; a rule drawn from that could refuse every save on other firmware.
    mismatched = (
        form.stack != link_stack
        or form.selected != shown
        or form.type != SETTING_LIST
        or target not in form.offered
    )
    if mismatched:
        raise FormMismatch(
            f"{setting.title}: the form is not the one of {shown} or lacks {target}"
        )

    async def shown_now() -> tuple[str, int]:
        read_back, (now_shown, _) = await _read(client, menu_path, setting_shown)
        return read_back, now_shown

    return await _save(client, form, setting, target, count_write, shown_now)


def own_form(text: str, path: str) -> pages.SettingForm | None:
    """The form of the setting whose own page is at path; None for any other.

    Another setting's form, or a form of another kind, served in its place is
    neither its reading nor sent.
    """
    form = pages.setting_form(text)
    if form is None or form.path != path or form.type != SETTING_LIST:
        return None
    return form


async def _write_form(
    client: Client,
    path: str,
    setting: Setting,
    target: int,
    count_write: Callable[[], object],
) -> str:
    """Set the setting whose own form is the page at path; that page afterwards.

    Only its own form counts, before the save and after it.
    """

    def form_here(text: str) -> pages.SettingForm | None:
        return own_form(text, path)

    try:
        page, form = await _read(client, path, form_here)
    except (Broken, Unreachable) as error:
        raise FormNotRead(str(error)) from error
    if form.selected == target:
        return page
    if target not in form.offered:
        raise FormMismatch(f"{setting.title}: the form lacks {target}")

    async def selected_now() -> tuple[str, int]:
        read_back, now = await _read(client, path, form_here)
        return read_back, now.selected

    return await _save(client, form, setting, target, count_write, selected_now)


async def _save(
    client: Client,
    form: pages.SettingForm,
    setting: Setting,
    target: int,
    count_write: Callable[[], object],
    shown_now: Callable[[], Awaitable[tuple[str, int]]],
) -> str:
    """Send the form with `target` once, and read back what the controller holds.

    The one save of every setting: nothing else sends a form.
    """
    await client.save(form, target, count_write)
    try:
        read_back, now_shown = await shown_now()
    except WebifError as error:
        # The save went out: "called off" or "login refused" alone would
        # invite the user to save again a value the controller may hold.
        raise NotReadBack(str(error)) from error
    if now_shown != target:
        raise OtherValueShown(
            read_back,
            setting.shown(now_shown),
            setting.shown(target),
            setting.title,
        )
    return read_back
