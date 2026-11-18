"""Two pages stored at one address: neither is polled nor written.

Which of the two the address is cannot be told, so both are left out until a
Reconfigure searches the pages again. The search while polling, which can
leave a page it did not find at an address it gave another, is covered in
test_webif_moved_pages.
"""

import logging

import pytest

from custom_components.weishaupt_modbus.const import CONF, CONST
from custom_components.weishaupt_modbus.webif.discovery import (
    HEAT_PUMP_PAGE,
    HEATING_CIRCUIT_PAGE,
    PAGE_MENUS,
    SECOND_GENERATOR_PAGE,
    STATISTICS_PAGE,
    circuit_settings_page,
    shared_addresses,
)
from custom_components.weishaupt_modbus.webif_sensor import EVOBLOCK
from custom_components.weishaupt_modbus.webif_shared import SHARED_ISSUE
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import issue_registry as ir

from .test_webif_settings_pages import ALL_PAGES, _number, evoblock_site
from .test_webif_setup import (
    PAGES,
    _enable_custom_integrations,  # noqa: F401
    _entries,
    _start,
    pump,  # noqa: F401
)
from .webif_stand_in import STACK

# A made-up address no page of the stand-in has.
SHARED = STACK + "0C000D1A000000000000000A0B020003000401"


def _issue(hass, entry):
    return ir.async_get(hass).async_get_issue(
        CONST.DOMAIN, f"{SHARED_ISSUE}_{entry.entry_id}"
    )


def test_the_pages_sharing_an_address_are_named():
    assert shared_addresses({"a": "x", "b": "y"}) == set()
    assert shared_addresses({"a": "x", "b": "x", "c": "y"}) == {"a", "b"}
    assert shared_addresses({"a": "x", "b": "x", "c": "x"}) == {"a", "b", "c"}


@pytest.mark.e2e
@pytest.mark.timeout(120)
async def test_two_optional_pages_stored_at_one_address_are_left_out(
    hass,
    pump,  # noqa: F811
    caplog,
):
    """Both are dropped from the entry before its entities are made, a notice
    asks for Reconfigure, and the rest is polled."""
    caplog.set_level(logging.INFO)
    web = _entries(hass, pump)
    hass.config_entries.async_update_entry(
        web,
        data={
            **web.data,
            CONF.PAGES: {
                **PAGES,
                HEATING_CIRCUIT_PAGE: SHARED,
                SECOND_GENERATOR_PAGE: SHARED,
            },
        },
    )

    await _start(hass, web)

    assert web.state is ConfigEntryState.LOADED
    assert web.data[CONF.PAGES] == PAGES
    issue = _issue(hass, web)
    assert issue is not None
    assert issue.translation_placeholders == {
        "pages": ", ".join(
            PAGE_MENUS[key]
            for key in sorted((HEATING_CIRCUIT_PAGE, SECOND_GENERATOR_PAGE))
        )
    }
    assert not [
        record
        for record in caplog.records
        if record.name.startswith("custom_components") and STACK in record.getMessage()
    ]


@pytest.mark.e2e
@pytest.mark.timeout(120)
async def test_a_required_page_stored_at_a_shared_address_does_not_start(
    hass,
    pump,  # noqa: F811
):
    """Only damaged data holds that: the entry does not start, asks nothing
    and keeps its pages as they are for Reconfigure to search again."""
    web = _entries(hass, pump)
    damaged = {**PAGES, STATISTICS_PAGE: PAGES[HEAT_PUMP_PAGE]}
    hass.config_entries.async_update_entry(web, data={**web.data, CONF.PAGES: damaged})
    pump.asked.clear()

    await hass.config_entries.async_setup(web.entry_id)
    await hass.async_block_till_done()

    assert web.state is ConfigEntryState.SETUP_ERROR
    assert web.data[CONF.PAGES] == damaged
    assert _issue(hass, web) is None
    assert pump.asked == []


@pytest.mark.e2e
@pytest.mark.timeout(120)
async def test_two_circuits_stored_at_one_settings_page_get_no_number(
    hass,
    pump,  # noqa: F811
):
    """Neither minimum can be set: no number is made for either circuit."""
    evoblock_site(pump)
    web = _entries(hass, pump)
    pages = {**ALL_PAGES, circuit_settings_page(3): ALL_PAGES[circuit_settings_page(2)]}
    hass.config_entries.async_update_entry(
        web, data={**web.data, CONF.MODEL: EVOBLOCK, CONF.PAGES: pages}
    )

    await _start(hass, web)

    assert web.state is ConfigEntryState.LOADED
    assert _number(hass, 2) is None
    assert _number(hass, 3) is None
    assert circuit_settings_page(2) not in web.data[CONF.PAGES]
    assert circuit_settings_page(3) not in web.data[CONF.PAGES]
    assert _issue(hass, web) is not None
