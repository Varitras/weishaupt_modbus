"""Home Assistant integration initialization."""

import copy
import logging
from typing import TYPE_CHECKING

from custom_components.weishaupt_modbus.weishaupt_modbus_api.modbus_api import (
    WeishauptModbusClient,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .configentry import MyData

if TYPE_CHECKING:
    from .configentry import MyConfigEntry

from .const import CONF, CONST, DEVICENAMES
from .coordinator import WeishauptModbusCoordinator
from .items import ModbusItem
from .kennfeld import PowerMap
from .migrate_helpers import migrate_entities
from .weishaupt_modbus_api.hpconst import (
    DEVICELISTS,
    MODBUS_HZ2_ITEMS,
    MODBUS_HZ3_ITEMS,
    MODBUS_HZ4_ITEMS,
    MODBUS_HZ5_ITEMS,
    MODBUS_HZ_ITEMS,
    MODBUS_IO_ITEMS,
    MODBUS_ST_ITEMS,
    MODBUS_SYS_ITEMS,
    MODBUS_W2_ITEMS,
    MODBUS_WP_ITEMS,
    MODBUS_WW_ITEMS,
)

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

    modbus_api = WeishauptModbusClient(host=entry.data[CONF.HOST], items=itemlist)

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

    hass.add_job(migrate_entities, entry, MODBUS_SYS_ITEMS, DEVICENAMES.SYS)
    hass.add_job(migrate_entities, entry, MODBUS_HZ_ITEMS, DEVICENAMES.HZ)
    hass.add_job(migrate_entities, entry, MODBUS_HZ2_ITEMS, DEVICENAMES.HZ2)
    hass.add_job(migrate_entities, entry, MODBUS_HZ3_ITEMS, DEVICENAMES.HZ3)
    hass.add_job(migrate_entities, entry, MODBUS_HZ4_ITEMS, DEVICENAMES.HZ4)
    hass.add_job(migrate_entities, entry, MODBUS_HZ5_ITEMS, DEVICENAMES.HZ5)
    hass.add_job(migrate_entities, entry, MODBUS_WP_ITEMS, DEVICENAMES.WP)
    hass.add_job(migrate_entities, entry, MODBUS_WW_ITEMS, DEVICENAMES.WW)
    hass.add_job(migrate_entities, entry, MODBUS_W2_ITEMS, DEVICENAMES.W2)
    hass.add_job(migrate_entities, entry, MODBUS_IO_ITEMS, DEVICENAMES.IO)
    hass.add_job(migrate_entities, entry, MODBUS_ST_ITEMS, DEVICENAMES.ST)

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

    hass.config_entries.async_update_entry(
        config_entry, data=new_data, minor_version=1, version=9
    )
    _LOGGER.warning("Config entries updated to version 9")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload entry."""
    # This is called when an entry/configured device is to be removed. The class
    # needs to unload itself, and remove callbacks. See the classes for further
    # details
    await entry.runtime_data.modbus_api.disconnect()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        try:
            hass.data[entry.data[CONF.PREFIX]].pop(entry.entry_id)
        except KeyError:
            _LOGGER.warning("KeyError: %s", str(entry.data[CONF.PREFIX]))

    return unload_ok
