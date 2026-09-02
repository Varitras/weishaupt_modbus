"""The unique id every entity of this integration carries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .const import CONF
from .weishaupt_modbus_api.const import DEFAULT_PORT

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
    return f"{entry_data[CONF.PREFIX]}{item_name}{device_postfix(entry_data)}"


def entry_unique_id(entry_data: Mapping[str, Any]) -> str:
    """One entry per pump: the endpoint, not the user-chosen names."""
    host = str(entry_data[CONF.HOST]).strip().lower()
    return f"{host}:{entry_data.get(CONF.PORT, DEFAULT_PORT)}"


def device_postfix(entry_data: Mapping[str, Any]) -> str:
    """The suffix a second pump's devices and ids carry, "" for the first."""
    postfix = entry_data[CONF.DEVICE_POSTFIX]
    return f"_{postfix}" if postfix else ""
