"""Home Assistant integration initialization."""

import copy
import logging

from custom_components.weishaupt_modbus.weishaupt_modbus_api.modbus_api import (
    WeishauptModbusClient,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import UNDEFINED
from homeassistant.util import slugify

from .configentry import MyConfigEntry, MyData
from .const import CONF, CONST
from .coordinator import WeishauptModbusCoordinator, write_budget
from .items import ModbusItem
from .kennfeld import PowerMap
from .migrate_helpers import unique_id_from_parts
from .weishaupt_modbus_api.hpconst import DEVICELISTS

_LOGGER = logging.getLogger(__name__)

# What the web-interface versions (5 to 8) stored in an entry, and the
# unique-id prefix of the entities they registered. Kept only so an old entry
# can be cleaned up; nothing else may refer to these.
LEGACY_WEBIF_KEYS = (
    "enable-webif",
    "username",
    "password",
    "Web-IF-Token",
    "Use-Mockup-Data",
    "Poll Heizkreis 1",
    "Poll Heizkreis 2",
    "Poll Heizkreis 3",
    "Poll Heizkreis 4",
    "Poll Heizkreis 5",
    "Poll Wärmepumpe",
    "Poll 2. Wärmeerzeuger",
    "Poll Statistik",
)
LEGACY_WEBIF_UNIQUE_ID_PREFIX = "webif_"

# Version 10 relabelled three 2nd-heat-source registers (see hpconst.py).
# The unique id carries the item name, so every recorded entity has to move
# to the new id. Each row: old item name, new item name, and the old and
# new display name per language - an entity id that still carries the old
# auto-generated slug is renamed along, a user-chosen one is left alone.
# The ORDER is load-bearing: 34103 gives up "Betriebsstunden E1" before
# 34106 claims it.
RENAMED_ITEMS = (
    (
        "Betriebsstunden E1",
        "Schaltspiele 2. WEZ",
        (
            ("Operation hours E1", "Switching cycles 2nd heat source"),
            ("Betriebsstunden E1", "Schaltspiele 2. WEZ"),
            ("Bedrijfsuren E1", "Schakelcycli 2e warmtebron"),
        ),
    ),
    (
        "Schaltspiele E-Heizung 1",
        "Betriebsstunden 2. WEZ",
        (
            ("Switching cycles E-heating 1", "Operation hours 2nd heat source"),
            ("Schaltspiele E-Heizung 1", "Betriebsstunden 2. WEZ"),
            ("Schakelcycli E-verwarming 1", "Bedrijfsuren 2e warmtebron"),
        ),
    ),
    (
        "Schaltspiele E-Heizung 2",
        "Betriebsstunden E1",
        (
            ("Switching cycles E-heating 2", "Operation hours E1"),
            ("Schaltspiele E-Heizung 2", "Betriebsstunden E1"),
            ("Schakelcycli E-verwarming 2", "Bedrijfsuren E1"),
        ),
    ),
)

PLATFORMS: list[str] = [
    "number",
    "select",
    "sensor",
    #    "switch",
]


async def async_setup_entry(hass: HomeAssistant, entry: MyConfigEntry) -> bool:
    """Set up entry."""
    # Independent copies per config entry: the items carry runtime state.
    itemlist: list[ModbusItem] = []
    for device in DEVICELISTS:
        itemlist.extend(copy.deepcopy(item) for item in device)

    modbus_api = WeishauptModbusClient(
        host=entry.data[CONF.HOST], items=itemlist, write_budget=write_budget(entry)
    )

    modbus_coordinator = WeishauptModbusCoordinator(
        hass=hass,
        client=modbus_api,
        api_items=itemlist,
        p_config_entry=entry,
    )
    await modbus_coordinator.async_config_entry_first_refresh()
    entry.runtime_data = MyData(
        modbus_api=modbus_api,
        config_dir=hass.config.config_dir,
        hass=hass,
        coordinator=modbus_coordinator,
        powermap=None,
    )

    powermap = PowerMap(entry, hass)
    await powermap.initialize()
    entry.runtime_data.powermap = powermap

    # see https://community.home-assistant.io/t/config-flow-how-to-update-an-existing-entity/522442/8
    entry.async_on_unload(entry.add_update_listener(update_listener))

    # This creates each HA object for each platform your device requires.
    # It's done by calling the `async_setup_entry` function in each platform module.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info("Init done")

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update listener."""
    await hass.config_entries.async_reload(
        entry.entry_id
    )  # list of entry_ids created for file


async def async_migrate_entry(hass: HomeAssistant, config_entry: MyConfigEntry) -> bool:
    """Migrate old entry."""

    new_data = {**config_entry.data}
    _LOGGER.warning(
        "Starting config migration process. Current version: %s", config_entry.version
    )

    if config_entry.version < 2:
        _LOGGER.warning("Version <2 detected")
        new_data[CONF.PREFIX] = CONST.DEF_PREFIX
        new_data[CONF.DEVICE_POSTFIX] = ""
        new_data[CONF.KENNFELD_FILE] = CONST.DEF_KENNFELDFILE
    if config_entry.version < 3:
        _LOGGER.warning("Version <3 detected")
        new_data[CONF.HK2] = False
        new_data[CONF.HK3] = False
        new_data[CONF.HK4] = False
        new_data[CONF.HK5] = False
    if config_entry.version < 4:
        _LOGGER.warning("Version <4 detected")
        new_data[CONF.NAME_DEVICE_PREFIX] = False
        new_data[CONF.NAME_TOPIC_PREFIX] = False

    if config_entry.version < 9:
        _LOGGER.warning("Version <9 detected")
        # Versions 5 to 8 added the web-interface settings; the web interface
        # is gone, and so are its keys and the entities it registered.
        for key in LEGACY_WEBIF_KEYS:
            new_data.pop(key, None)
        registry = er.async_get(hass)
        for registry_entry in er.async_entries_for_config_entry(
            registry, config_entry.entry_id
        ):
            if registry_entry.unique_id.startswith(LEGACY_WEBIF_UNIQUE_ID_PREFIX):
                registry.async_remove(registry_entry.entity_id)

    if config_entry.version < 10:
        _LOGGER.warning("Version <10 detected")
        _move_relabelled_entities(hass, new_data)

    hass.config_entries.async_update_entry(
        config_entry, data=new_data, minor_version=1, version=10
    )
    _LOGGER.warning("Config entries updated to version 10")
    return True


def _move_relabelled_entities(hass: HomeAssistant, entry_data: dict) -> None:
    registry = er.async_get(hass)
    for old_name, new_name, labels in RENAMED_ITEMS:
        old_unique_id = unique_id_from_parts(entry_data, old_name)
        entity_id = registry.async_get_entity_id("sensor", CONST.DOMAIN, old_unique_id)
        if entity_id is None:
            continue
        new_entity_id = _entity_id_with_new_label(entity_id, labels)
        registry.async_update_entity(
            entity_id,
            new_unique_id=unique_id_from_parts(entry_data, new_name),
            new_entity_id=new_entity_id or UNDEFINED,
        )
        _LOGGER.warning(
            "Register relabelled: %s is now %s (entity %s -> %s)",
            old_name,
            new_name,
            entity_id,
            new_entity_id or f"{entity_id} (kept, it was renamed by hand)",
        )


def _entity_id_with_new_label(entity_id: str, labels: tuple) -> str | None:
    """The entity id with the old auto-generated slug swapped for the new one.

    None when the id no longer carries that slug in any language.
    """
    domain, object_id = entity_id.split(".", 1)
    for old_label, new_label in labels:
        old_slug = slugify(old_label)
        if old_slug in object_id:
            return f"{domain}.{object_id.replace(old_slug, slugify(new_label))}"
    return None


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload entry."""
    # This is called when an entry/configured device is to be removed. The class
    # needs to unload itself, and remove callbacks. See the classes for further
    # details
    await entry.runtime_data.modbus_api.disconnect()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
