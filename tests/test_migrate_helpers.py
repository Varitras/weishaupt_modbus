"""The unique id: the string a recorded history is keyed on, pinned to the letter."""

from types import SimpleNamespace

from custom_components.weishaupt_modbus.const import CONF, DEVICES, FORMATS, TYPES
from custom_components.weishaupt_modbus.items import ModbusItem
from custom_components.weishaupt_modbus.migrate_helpers import create_unique_id


def _entry(postfix=""):
    return SimpleNamespace(
        data={CONF.PREFIX: "weishaupt_wbb", CONF.DEVICE_POSTFIX: postfix}
    )


ITEM = ModbusItem(
    30001,
    "Aussentemperatur",
    FORMATS.TEMPERATURE,
    TYPES.SENSOR,
    DEVICES.SYS,
    "aussentemp",
)


def test_unique_id_is_prefix_name_and_postfix():
    assert create_unique_id(_entry(), ITEM) == "weishaupt_wbbAussentemperatur"
    assert create_unique_id(_entry(postfix="keller"), ITEM) == (
        "weishaupt_wbbAussentemperatur_keller"
    )
