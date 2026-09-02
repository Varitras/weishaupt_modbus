"""The unique id every entity of this integration carries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .const import CONF

if TYPE_CHECKING:
    from .configentry import MyConfigEntry
    from .items import ModbusItem


def create_unique_id(config_entry: MyConfigEntry, modbus_item: ModbusItem) -> str:
    """Prefix, item name and device postfix - the id history is keyed on.

    Unchanged since the first release on purpose: a different string would
    orphan every recorded entity. The entity *id* is Home Assistant's business
    and is never touched outside a versioned entry migration (issue #146).
    """
    return unique_id_from_parts(config_entry.data, modbus_item.name)


def unique_id_from_parts(entry_data: Mapping[str, Any], item_name: str) -> str:
    """The same id for an item name that is no longer in the table."""
    dev_postfix = f"_{entry_data[CONF.DEVICE_POSTFIX]}"

    if dev_postfix == "_":
        dev_postfix = ""

    return f"{entry_data[CONF.PREFIX]}{item_name}{dev_postfix}"
