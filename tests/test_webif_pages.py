"""What a web interface page says, and how a broken one is told apart.

The pages here are synthetic, shaped like the controller's (EC-BIBLOCK COM
V5.5); recorded pages carry a serial number, an access code and addresses
and never enter the repository.
"""

import pytest

from custom_components.weishaupt_modbus.webif import pages

MAIN = "0C000001000000000080000A0B010002000301"
HEAT_PUMP = "0C000C22000000000000000A0B020003000401"
PUMP_MENU = "64000001000000000080000A0B010002000301"
HEATING = "64001800000000000080000A0B020003000401"
LIMIT = "64001807000000003C40000A0B030011010401"
SIBLING = "64001900000000000080000A0B020003000401"
SIBLING_CHILD = "64001901000000002D40000A0B030011010401"
GRANDCHILD = "64001807010000000000000A0B040011010401"
HEATING_PATH = "/settings_export.html?stack=" + f"{PUMP_MENU},{HEATING}"
REQUIRED = frozenset({"Hochdruck", "Verdichter"})


def link(segments, title, shown=""):
    href = "/settings_export.html?stack=" + ",".join(segments)
    return f'<a class="nav-link browseobj" href="{href}" role="tab">\n<h5>{title}</h5>\n{shown}\n</a>\n'


def value(title, text):
    return f'<div class="nav-link browseobj" role="tab">\n<h5>{title}</h5>\n{text}\n</div>\n'


def column(inner):
    return f'<div class="col-3">\n<div class="nav flex-column nav-pills" role="tablist">{inner}</div></div>\n'


MENU = column(link([MAIN], "Info") + link([PUMP_MENU], "Wärmepumpe"))
HEAT_PUMP_PAGE = MENU + column(
    value("Betrieb", "Warmwasserbetrieb")
    + value("Hochdruck", "24.4 BAR")
    + value("Verdichter", "2568 rpm")
    + value("Ist Leistung", "Aus")
)


def test_the_values_of_a_whole_page_are_read_in_page_order():
    assert pages.values(HEAT_PUMP_PAGE) == [
        ("Betrieb", "Warmwasserbetrieb"),
        ("Hochdruck", "24.4 BAR"),
        ("Verdichter", "2568 rpm"),
        ("Ist Leistung", "Aus"),
    ]
    assert pages.is_complete(pages.values(HEAT_PUMP_PAGE), REQUIRED)


def test_a_page_with_its_menu_instead_of_its_values_is_not_complete():
    """A slow internal read answered 200 with the main menu where the
    values belonged - no error, no warning in the status."""
    nested = MENU + column(
        link([MAIN, PUMP_MENU], "Wärmepumpe")
        + link([MAIN, "06000001000000000080000A0B010011000301"], "Systembetriebsart")
    )

    assert pages.values(nested) == []
    assert not pages.is_complete(pages.values(nested), REQUIRED)


def test_a_page_showing_nothing_is_never_complete():
    """The controller's empty column, even for a page that requires no title."""
    empty = MENU + column("")

    assert pages.values(empty) == []
    assert not pages.is_complete(pages.values(empty), frozenset())


def test_an_entry_left_without_its_value_makes_the_page_incomplete():
    blank = MENU + column(value("Hochdruck", "24.4 BAR") + value("Verdichter", ""))

    assert not pages.is_complete(pages.values(blank), REQUIRED)


def test_the_values_of_another_section_do_not_pass_for_the_heat_pump():
    """The controller sometimes sends a different section's page."""
    statistics = MENU + column(
        value("JAZ Jahr", "4.16") + value("elektrische Energie Jahr", "1642.482 KWh")
    )

    assert not pages.is_complete(pages.values(statistics), REQUIRED)


def test_an_entry_left_open_does_not_spill_into_the_next():
    """A new entry reset its title and text, not whether it was still inside
    a title: an unclosed <h5> made the next entry's value part of its title."""
    page = column(
        '<div class="nav-link browseobj"><h5>Hochdruck</div>'
        '<div class="nav-link browseobj">2568 rpm<h5>Verdichter</h5></div>'
    )

    assert pages.values(page) == [("Hochdruck", ""), ("Verdichter", "2568 rpm")]


def test_an_anchor_entry_without_a_link_closes_on_its_own_tag():
    """An <a> entry without href was counted as a <div>, never closed, and
    swallowed the entries after it."""
    page = column(
        '<a class="nav-link browseobj" role="tab"><h5>Hochdruck</h5>24.4 BAR</a>'
        + value("Verdichter", "2568 rpm")
    )

    assert pages.values(page) == [("Hochdruck", "24.4 BAR"), ("Verdichter", "2568 rpm")]


def test_a_class_that_only_contains_the_marker_is_no_entry():
    page = column(
        '<div class="browseobjects"><h5>Hochdruck</h5>24.4 BAR</div>'
        + value("Verdichter", "2568 rpm")
    )

    assert pages.values(page) == [("Verdichter", "2568 rpm")]


def test_a_title_is_normalised_like_its_text():
    """A title broken across lines never matched its sensor's."""
    page = column(value("Betriebsstd.\n   Verdichter", "16847 h"))

    assert pages.values(page) == [("Betriebsstd. Verdichter", "16847 h")]


def test_a_page_showing_a_sensor_title_twice_is_not_complete():
    """The sensor read whichever came last, and nothing said the page was odd."""
    found = [
        ("Hochdruck", "24.4 BAR"),
        ("Verdichter", "2568 rpm"),
        ("Hochdruck", "12.0 BAR"),
    ]

    assert not pages.is_complete(found, REQUIRED)


def test_a_menu_shows_its_children_with_their_values():
    page = (
        MENU
        + column(link([PUMP_MENU, HEATING], "Heizen"))
        + column(
            link(
                [PUMP_MENU, HEATING, "64001806000000002D40000A0B030011010401"],
                "Schaltdifferenz",
                "4.5 K",
            )
            + link([PUMP_MENU, HEATING, LIMIT], "Leistungsbegrenzung", "60 %")
        )
    )

    shown = {entry.title: entry.text for entry in pages.children(page, HEATING_PATH)}

    assert shown == {"Schaltdifferenz": "4.5 K", "Leistungsbegrenzung": "60 %"}


def test_the_main_menus_are_the_links_of_one_segment():
    """The broken answer that nests the main menus gives them two segments."""
    nested = MENU + column(link([MAIN, PUMP_MENU], "Wärmepumpe"))

    assert pages.main_menus(nested) == [
        ("Info", "/settings_export.html?stack=" + MAIN),
        ("Wärmepumpe", "/settings_export.html?stack=" + PUMP_MENU),
    ]


def test_a_link_that_is_no_stack_is_no_main_menu():
    """Discovery follows these links, and the client may fetch the logout
    page as well: an Info entry leading there would end the session."""
    page = column(
        '<a class="nav-link browseobj" href="/logout.html"><h5>Info</h5></a>'
        + link([PUMP_MENU], "Wärmepumpe")
    )

    assert pages.main_menus(page) == [
        ("Wärmepumpe", "/settings_export.html?stack=" + PUMP_MENU)
    ]


def test_siblings_nested_one_level_too_deep_are_no_children():
    """The broken answer lists the parent's siblings below it, still
    carrying their own depth in the segment."""
    page = MENU + column(link([PUMP_MENU, HEATING, SIBLING], "Kühlen"))

    assert pages.children(page, HEATING_PATH) == []


def test_the_entries_of_another_menu_are_no_children():
    """The controller sometimes sends another section in place of the one
    asked for; its entries have the right depth under the wrong parent."""
    page = MENU + column(
        link([PUMP_MENU, SIBLING, SIBLING_CHILD], "Leistungsbegrenzung", "30 %")
    )

    assert pages.children(page, HEATING_PATH) == []


def test_a_grandchild_is_no_child():
    """The depth its own segment names agrees with where it sits; only the
    number of segments tells it from a child."""
    page = MENU + column(
        link([PUMP_MENU, HEATING, LIMIT, GRANDCHILD], "Leistungsbegrenzung", "30 %")
    )

    assert pages.children(page, HEATING_PATH) == []


@pytest.mark.parametrize(
    ("shown", "expected"),
    [
        ("24.4 BAR", 24.4),
        ("2568 rpm", 2568.0),
        ("-4.0 K", -4.0),
        ("5356.310 KWh", 5356.31),
        ("41 %", 41.0),
        ("Aus", None),
        ("--", None),
        ("Warmwasser", None),
    ],
)
def test_a_shown_value_becomes_a_number(shown, expected):
    assert pages.number(shown) == expected
