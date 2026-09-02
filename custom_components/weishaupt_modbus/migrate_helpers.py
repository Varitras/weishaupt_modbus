"""The unique id every entity of this integration carries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import CONF

if TYPE_CHECKING:
    from .configentry import MyConfigEntry
    from .items import ModbusItem


def create_unique_id(config_entry: MyConfigEntry, modbus_item: ModbusItem) -> str:
    """Prefix, item name and device postfix - the id history is keyed on.

    Unchanged since the first release on purpose: a different string would
    orphan every recorded entity. The entity *id* is Home Assistant's business
    and is never touched by this integration (issue #146).
    """
    dev_postfix = f"_{config_entry.data[CONF.DEVICE_POSTFIX]}"

    if dev_postfix == "_":
        dev_postfix = ""

    return f"{config_entry.data[CONF.PREFIX]}{modbus_item.name}{dev_postfix}"
