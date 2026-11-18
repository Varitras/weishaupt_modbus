"""Pages stored at one address: left out until a Reconfigure searches again.

A value read or written at such an address could belong to either page.
"""

from collections.abc import Collection, Mapping
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import issue_registry as ir

from .const import CONF, CONST
from .webif.discovery import OPTIONAL_PAGES, PAGE_MENUS, shared_addresses

_LOGGER = logging.getLogger(__name__)

SHARED_ISSUE = "webif_pages_shared"


def _issue_id(entry_id: str) -> str:
    return f"{SHARED_ISSUE}_{entry_id}"


def leave_out(
    hass: HomeAssistant,
    entry: ConfigEntry,
    keys: Collection[str],
    pages: Mapping[str, str],
) -> None:
    """Store `pages` without `keys`, and name these in a notice."""
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF.PAGES: {key: path for key, path in pages.items() if key not in keys},
        },
    )
    left_out = sorted(keys)
    # By key, without the addresses, which carry the controller's codes.
    _LOGGER.info(
        "Pages at one address left out until a reconfigure: %s", ", ".join(left_out)
    )
    ir.async_create_issue(
        hass,
        CONST.DOMAIN,
        _issue_id(entry.entry_id),
        is_fixable=False,
        # The pages stay out after a restart, and so does the reason.
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=SHARED_ISSUE,
        translation_placeholders={
            "pages": ", ".join(PAGE_MENUS[key] for key in left_out)
        },
    )


def leave_out_stored(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Before the entry polls, the pages stored at one address go.

    Which of them the address is cannot be told. A page the entry must read
    among them keeps it from starting: only damaged data holds that.
    """
    pages = entry.data[CONF.PAGES]
    shared = shared_addresses(pages)
    if not shared:
        return
    if shared - set(OPTIONAL_PAGES):
        raise ConfigEntryError(
            translation_domain=CONST.DOMAIN, translation_key="webif_pages_shared"
        )
    leave_out(hass, entry, shared, pages)


def clear_shared_notice(hass: HomeAssistant, entry_id: str) -> None:
    """The pages were searched anew, or the entry is gone."""
    ir.async_delete_issue(hass, CONST.DOMAIN, _issue_id(entry_id))
