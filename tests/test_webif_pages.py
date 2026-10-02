"""What a web interface page says, and how a broken one is told apart.

The pages here are synthetic, shaped like the controller's (EC-BIBLOCK COM
V5.5); recorded pages carry a serial number, an access code and addresses
and never enter the repository.
"""

import pytest

from custom_components.weishaupt_modbus.webif import pages

MAIN = "0C000001000000000080000F4C010002000301"
HEAT_PUMP = "0C000C22000000000000000F4C020003000401"
PUMP_MENU = "64000001000000000080000F4C010002000301"
HEATING = "64001800000000000080000F4C020003000401"
LIMIT = "64001807000000003C40000F4C030011010401"
SIBLING = "64001900000000000080000F4C020003000401"
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
        + link([MAIN, "06000001000000000080000F4C010011000301"], "Systembetriebsart")
    )

    assert pages.values(nested) == []
    assert not pages.is_complete(pages.values(nested), REQUIRED)


def test_an_entry_left_without_its_value_makes_the_page_incomplete():
    blank = MENU + column(value("Hochdruck", "24.4 BAR") + value("Verdichter", ""))

    assert not pages.is_complete(pages.values(blank), REQUIRED)


def test_the_values_of_another_section_do_not_pass_for_the_heat_pump():
    """The controller sometimes sends a different section's page."""
    statistics = MENU + column(
        value("JAZ Jahr", "4.16") + value("elektrische Energie Jahr", "1642.482 KWh")
    )

    assert not pages.is_complete(pages.values(statistics), REQUIRED)


def test_the_selected_option_is_a_setting_s_value():
    form = (
        '<form action="pro_save.html" method="POST"><input type="hidden" name="id" value="x">'
        '<select class="form-control" name="value">\n<option value="59">\n59</option>\n'
        '<option value="60" selected>\n60</option>\n<option value="61">\n61</option>\n</select>'
        '<button type="submit" class="btn btn-success">Speichern</button></form>'
    )
    page = MENU + column(
        f'<div class="nav-link browseobj" role="tab">\n<h5>Leistungsbegrenzung</h5>\n{form}\n</div>'
    )

    assert pages.values(page) == [("Leistungsbegrenzung", "60")]


def test_a_menu_shows_its_children_with_their_values():
    parent = "/settings_export.html?stack=" + f"{PUMP_MENU},{HEATING}"
    page = (
        MENU
        + column(link([PUMP_MENU, HEATING], "Heizen"))
        + column(
            link(
                [PUMP_MENU, HEATING, "64001806000000002D40000F4C030011010401"],
                "Schaltdifferenz",
                "4.5 K",
            )
            + link([PUMP_MENU, HEATING, LIMIT], "Leistungsbegrenzung", "60 %")
        )
    )

    shown = {entry.title: entry.text for entry in pages.children(page, parent)}

    assert shown == {"Schaltdifferenz": "4.5 K", "Leistungsbegrenzung": "60 %"}


def test_siblings_nested_one_level_too_deep_are_no_children():
    """The broken answer lists the parent's siblings below it, still
    carrying their own depth in the segment."""
    parent = "/settings_export.html?stack=" + f"{PUMP_MENU},{HEATING}"
    page = MENU + column(link([PUMP_MENU, HEATING, SIBLING], "Kühlen"))

    assert pages.children(page, parent) == []


@pytest.mark.parametrize(
    ("shown", "expected"),
    [
        ("24.4 BAR", 24.4),
        ("2568 rpm", 2568.0),
        ("-4.0 K", -4.0),
        ("5356.310 KWh", 5356.31),
        ("41 %", 41.0),
        ("Aus", 0.0),
        ("--", None),
        ("Warmwasser", None),
    ],
)
def test_a_shown_value_becomes_a_number(shown, expected):
    assert pages.number(shown) == expected
