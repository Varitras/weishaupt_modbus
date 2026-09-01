"""Entity ids and unique ids: the strings a migration has to reproduce exactly.

A unique id that comes out one character different orphans the entity's
history, so these are pinned to the letter.
"""

from types import SimpleNamespace

from custom_components.weishaupt_modbus.const import CONF, DEVICES, FORMATS, TYPES
from custom_components.weishaupt_modbus.items import ModbusItem
from custom_components.weishaupt_modbus.migrate_helpers import (
    create_new_entity_id,
    create_unique_id,
)


def _entry(postfix="", device_prefix=False, topic_prefix=False):
    return SimpleNamespace(
        data={
            CONF.PREFIX: "weishaupt_wbb",
            CONF.DEVICE_POSTFIX: postfix,
            CONF.NAME_DEVICE_PREFIX: device_prefix,
            CONF.NAME_TOPIC_PREFIX: topic_prefix,
        }
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


def test_entity_id_is_slugified_from_device_and_name():
    assert create_new_entity_id(_entry(), ITEM, "sensor", "WH System") == (
        "sensor.wh_system_aussentemperatur"
    )


def test_entity_id_carries_the_optional_prefixes_and_postfix():
    entry = _entry(postfix="keller", device_prefix=True, topic_prefix=True)

    assert create_new_entity_id(entry, ITEM, "sensor", "WH System") == (
        "sensor.wh_system_keller_sys_weishaupt_wbb_aussentemperatur"
    )
