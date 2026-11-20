"""A circuit's settings page is the circuit's only while its main menu says so.

Every circuit's settings page shows the same rows, so its titles cannot tell
whose it is. When the controller retires a circuit and gives its address to
another one, the page at the old address comes whole and asks for no search;
only the main menu its address leads from still names the circuit it belongs
to. Synthetic loopback stand-in only.
"""

import pytest

from homeassistant.exceptions import HomeAssistantError

from . import test_webif_settings_pages
from .test_webif_settings_pages import (
    CONF,
    HOT_WATER_MENU,
    INFO,
    NAMES,
    PASSWORD,
    SETTINGS,
    STACK,
    THIRD_MENU,
    USER,
    _enable_custom_integrations,  # noqa: F401
    _number,
    _start,
    _web_entry,
    circuit_settings_page,
    column,
    entry_segment,
    link,
    main_menus,
    menu_page,
    minimum_leaf,
    overview,
    read_web_interface,
    settings_page,
    webif,
)
from .test_webif_setup import Clock

# The stand-in pump and the short waits of the settings pages' tests.
pump = test_webif_settings_pages.pump
quick = test_webif_settings_pages.quick

SECOND = circuit_settings_page(2)
THIRD = circuit_settings_page(3)
TARGET = 34.5
A_ROUND_LATER = 3 * 3600


def give_the_third_address_to_the_second(pump):
    """The controller drops circuit 3 and lists circuit 2 under its menu code.

    Its overview, Info and menus agree with each other: a fresh setup finds
    circuit 2 at the address circuit 3 had.
    """
    pump.renumber("BEEF")
    overview_now = pump.current(main_menus()) + column(
        link([THIRD_MENU], "Heizkreis 2", NAMES[2])
        + link([pump.current(HOT_WATER_MENU)], "Warmwasser")
    )
    pump.site[webif.OVERVIEW] = overview_now
    pump.pages = [overview_now]
    info = pump.current(STACK + INFO)
    pump.site[info] = pump.site[info].replace("Heizkreis 3", "Unused")
    pump.site[STACK + THIRD_MENU] = menu_page(THIRD_MENU, "Einstellungen").replace(
        overview(), overview_now
    )
    owner = pump.circuits[2]

    def show():
        path = SETTINGS[THIRD]
        pump.site[path] = settings_page(THIRD_MENU, owner.rows, owner.values).replace(
            overview(), overview_now
        )
        row = [title for title, _ in owner.rows].index("Minimaltemperatur")
        segment = entry_segment(THIRD_MENU, row, owner.values["Minimaltemperatur"])
        pump.site[f"{path},{segment}"] = minimum_leaf(
            THIRD_MENU, owner.rows, owner.values
        ).replace(overview(), overview_now)

    def save(form):
        owner.values["Minimaltemperatur"] = f"{int(form['value']) / 10:.1f} °C"
        show()
        return SETTINGS[THIRD]

    show()
    pump.saves_elsewhere = save


async def _set(hass, circuit):
    entity_id = _number(hass, circuit)
    if entity_id is None:
        return None
    try:
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": TARGET},
            blocking=True,
        )
    except HomeAssistantError as error:
        return error.translation_key
    return None


async def _entry_after_the_move(hass, pump, *, second_seen_at_setup):
    if not second_seen_at_setup:
        pump.site[webif.OVERVIEW] = overview().replace("Heizkreis 2", "Unavailable")
    found = await read_web_interface(hass, pump.host, USER, PASSWORD)
    assert (SECOND in found.pages) == second_seen_at_setup
    entry = _web_entry(hass, pump)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF.PAGES: found.pages}
    )
    await _start(hass, entry)
    clock = Clock()
    entry.runtime_data.coordinator._clock = clock
    give_the_third_address_to_the_second(pump)
    clock.now += A_ROUND_LATER
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


@pytest.mark.parametrize("reload", [False, True], ids=["running", "reloaded"])
@pytest.mark.parametrize(
    "second_seen_at_setup", [False, True], ids=["second unseen", "second seen"]
)
async def test_a_retired_circuit_neither_shows_nor_sets_the_circuit_now_at_its_address(
    hass, pump, quick, reload, second_seen_at_setup
):
    entry = await _entry_after_the_move(
        hass, pump, second_seen_at_setup=second_seen_at_setup
    )
    if reload:
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    entity_id = _number(hass, 3)
    if entity_id is not None:
        assert hass.states.get(entity_id).state != "20.0"
    await _set(hass, 3)

    assert pump.saved == []
    assert pump.circuits[2].values["Minimaltemperatur"] == "20.0 °C"
