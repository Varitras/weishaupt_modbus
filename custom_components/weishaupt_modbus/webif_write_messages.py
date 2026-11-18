"""How a write of a setting ended, as the callers that asked for it are told."""

from typing import NoReturn

from homeassistant.exceptions import HomeAssistantError

from .const import CONST
from .webif.client import Closed, LoginRefused, MaybeSaved, NotSaved
from .webif.setting import FormMismatch, FormNotRead, NotReadBack, OtherValueShown


def raise_translated(error: Exception, setting: str) -> NoReturn:
    """The way a write of `setting` ended, as its callers are told it."""
    match error:
        case HomeAssistantError():
            raise error
        case FormNotRead():
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_write_form_not_read",
                translation_placeholders={"setting": setting},
            ) from error
        case FormMismatch():
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_write_form_mismatch",
                translation_placeholders={"setting": setting},
            ) from error
        case NotSaved():
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_write_not_saved",
                translation_placeholders={"setting": setting},
            ) from error
        case MaybeSaved():
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_write_maybe_saved",
                translation_placeholders={"setting": setting},
            ) from error
        case OtherValueShown():
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_write_other_value",
                translation_placeholders={
                    "setting": setting,
                    "shown": error.shown_text,
                    "target": error.target_text,
                },
            ) from error
        case NotReadBack():
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_write_not_read_back",
                translation_placeholders={"setting": setting},
            ) from error
        case LoginRefused():
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_write_login_refused",
            ) from error
        case Closed():
            raise HomeAssistantError(
                translation_domain=CONST.DOMAIN,
                translation_key="webif_write_aborted",
                translation_placeholders={"setting": setting},
            ) from error
    raise HomeAssistantError(
        translation_domain=CONST.DOMAIN,
        translation_key="webif_write_failed",
        translation_placeholders={"setting": setting},
    ) from error
