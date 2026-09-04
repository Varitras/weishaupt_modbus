"""The item model: number-to-text lookups in both directions."""

from custom_components.weishaupt_modbus.const import DEVICES, FORMATS, TYPES
from custom_components.weishaupt_modbus.items import ModbusItem, StatusItem

STATES = [
    StatusItem(number=0, text="undefiniert", translation_key="mode_undefined"),
    StatusItem(number=19, text="Heizbetrieb", translation_key="mode_heating"),
]


def _item():
    return ModbusItem(
        30006,
        "Betriebsanzeige",
        FORMATS.STATUS,
        TYPES.SENSOR,
        DEVICES.SYS,
        "betriebsanzeige",
        resultlist=STATES,
    )


def test_text_from_number():
    assert _item().get_text_from_number(19) == "Heizbetrieb"


def test_translation_key_from_number():
    assert _item().get_translation_key_from_number(19) == "mode_heating"


def test_an_unknown_number_names_itself():
    assert _item().get_text_from_number(43) == "unbekannt <43>"
    assert _item().get_translation_key_from_number(43) == "unbekannt <43>"


def test_number_from_text_and_key():
    assert _item().get_number_from_text("Heizbetrieb") == 19
    assert _item().get_number_from_translation_key("mode_heating") == 19


def test_an_unknown_text_is_none_not_a_number_that_could_be_written():
    """-1 used to come back here, and the select entity wrote it to the bus."""
    assert _item().get_number_from_text("Kühlen") is None
    assert _item().get_number_from_translation_key("mode_cooling") is None


def test_none_is_tolerated_everywhere():
    item = _item()

    assert item.get_text_from_number(None) is None
    assert item.get_translation_key_from_number(None) is None
    assert item.get_number_from_translation_key(None) is None


def test_an_item_without_a_result_list_answers_none():
    item = ModbusItem(30001, "x", FORMATS.NUMBER, TYPES.SENSOR, DEVICES.SYS, "k")

    assert item.get_text_from_number(1) is None
    assert item.get_number_from_text("x") is None


def test_a_fresh_item_is_valid():
    item = ModbusItem(30001, "x", FORMATS.NUMBER, TYPES.SENSOR, DEVICES.SYS, "k")

    assert item.is_invalid is False
    assert item.state is None
    assert item.params == {}
