"""Writing the heating power limit, step by step, against a scripted client.

The pages are the stand-in's synthetic ones; what the client itself retries
is the client's tests' business.
"""

import pytest

from custom_components.weishaupt_modbus.webif import pages, setting
from custom_components.weishaupt_modbus.webif.client import (
    Broken,
    LoginRefused,
    MaybeSaved,
    NotSaved,
    Unreachable,
)

from .webif_stand_in import (
    HEATING,
    HEATING_PATH,
    PUMP_MENU,
    heating_page,
    limit_leaf,
    limit_segment,
)

LEAF = f"{HEATING_PATH},{limit_segment(60)}"


class ScriptedClient:
    """Answers each address from a script, the last answer for good.

    Every page asked for, every save and the count the write makes go into
    one list, in the order they happen.
    """

    def __init__(self, events, heating, leaf):
        self.events = events
        self.answers = {HEATING_PATH: list(heating), LEAF: [leaf]}
        self.save_error = None

    async def page(self, path, complete):
        self.events.append(("page", path))
        answers = self.answers[path]
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, BaseException):
            raise answer
        if not complete(answer):
            raise Broken(path)
        return answer

    async def save(self, form, value):
        self.events.append(("save", value))
        if self.save_error is not None:
            raise self.save_error


def whole(text):
    """The heating page's own rule, as far as these pages need it."""
    return bool(pages.children(text, HEATING_PATH))


def scripted(heating=(heating_page(60), heating_page(61)), leaf=limit_leaf(60)):
    events = []
    return ScriptedClient(events, heating, leaf), events


async def write(client, target=61):
    return await setting.write_power_limit(
        client,
        HEATING_PATH,
        whole,
        target,
        lambda: client.events.append(("count",)),
    )


async def test_a_write_reads_the_form_saves_once_and_reads_back():
    """Live, 2026-10-02: the heating page, the limit's own page, one save,
    the heating page again showing the new value."""
    client, events = scripted()

    assert await write(client) == heating_page(61)
    assert events == [
        ("page", HEATING_PATH),
        ("page", LEAF),
        ("count",),
        ("save", 61),
        ("page", HEATING_PATH),
    ]


STACK_FIELD = f'value="{PUMP_MENU},{HEATING},{limit_segment(60)}"'


@pytest.mark.parametrize(
    ("leaf", "target"),
    [
        (
            limit_leaf(60).replace(
                STACK_FIELD, f'value="{PUMP_MENU},{HEATING},{limit_segment(59)}"'
            ),
            61,
        ),
        (
            limit_leaf(60)
            .replace('value="60" selected', 'value="60"')
            .replace('<option value="59">', '<option value="59" selected>'),
            61,
        ),
        (limit_leaf(60).replace('value="para_list"', 'value="para_text"'), 61),
        (limit_leaf(60, offered=range(20, 101)), 15),
    ],
    ids=[
        "the form's stack is not the link's",
        "the form selects another value than shown",
        "the form is no list choice",
        "the target is not offered",
    ],
)
async def test_a_form_that_does_not_match_sends_nothing(leaf, target):
    """The form must be the one of the value the heating page shows: an id
    or a stack of another value is a save nobody has tried."""
    client, events = scripted(leaf=leaf)

    with pytest.raises(setting.FormMismatch):
        await write(client, target)

    assert ("count",) not in events
    assert not [event for event in events if event[0] == "save"]


@pytest.mark.parametrize(
    ("unreadable", "asked"),
    [
        ("heating", [("page", HEATING_PATH)]),
        ("leaf", [("page", HEATING_PATH), ("page", LEAF)]),
    ],
)
@pytest.mark.parametrize("error", [Broken(HEATING_PATH), Unreachable("TimeoutError")])
async def test_an_unreadable_page_sends_nothing(unreadable, asked, error):
    heating = [error] if unreadable == "heating" else [heating_page(60)]
    leaf = error if unreadable == "leaf" else limit_leaf(60)
    client, events = scripted(heating=heating, leaf=leaf)

    with pytest.raises(setting.FormNotRead):
        await write(client)

    assert events == asked


async def test_a_heating_page_without_the_limit_sends_nothing():
    """Another language or menu: the entry is found by its title or not at all."""
    page = heating_page(60).replace("Leistungsbegrenzung", "Power limit")
    client, events = scripted(heating=[page])

    with pytest.raises(setting.FormNotRead):
        await write(client)

    assert events == [("page", HEATING_PATH)]


async def test_a_target_the_controller_already_shows_is_not_written():
    """Owner decision, 2026-10-09: read fresh, and write only what differs."""
    client, events = scripted(heating=[heating_page(61)])

    assert await write(client) == heating_page(61)
    assert events == [("page", HEATING_PATH)]


@pytest.mark.parametrize("error", [NotSaved("lost"), MaybeSaved("TimeoutError")])
async def test_a_save_without_a_clear_answer_ends_the_write(error):
    """Counted, since it may have reached the EEPROM; nothing follows it."""
    client, events = scripted()
    client.save_error = error

    with pytest.raises(type(error)):
        await write(client)

    assert events[-2:] == [("count",), ("save", 61)]


async def test_a_read_back_showing_another_value_says_which():
    client, _ = scripted(heating=[heating_page(60), heating_page(65)])

    with pytest.raises(setting.OtherValueShown) as raised:
        await write(client)

    assert (raised.value.shown, raised.value.target) == (65, 61)
    assert raised.value.text == heating_page(65)


async def test_a_read_back_that_fails_is_reported_as_saved_but_not_read():
    client, events = scripted(heating=[heating_page(60), Broken(HEATING_PATH)])

    with pytest.raises(setting.NotReadBack):
        await write(client)

    assert ("save", 61) in events


async def test_a_refused_login_is_no_unreadable_form():
    """The polling's own answer to it is to stop and ask for the login."""
    client, _ = scripted(heating=[LoginRefused("wrong password")])

    with pytest.raises(LoginRefused):
        await write(client)


@pytest.mark.parametrize(
    ("shown", "expected"),
    [("60 %", 60), ("100 %", 100), ("--", None), ("60 K", None), ("60.5 %", None)],
)
def test_the_power_limit_is_read_as_a_whole_percentage(shown, expected):
    assert setting.shown_percent(shown) == expected
