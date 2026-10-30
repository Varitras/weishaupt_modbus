"""Failed refreshes that are no fault, logged at INFO instead of ERROR."""

import logging

from homeassistant.helpers.update_coordinator import UpdateFailed


class ExpectedOutage(UpdateFailed):
    """A failed refresh that is no fault: the pump went away, or polling paused."""


class _ExpectedOutageIsNoError(logging.Filter):
    """An expected outage is logged at INFO, like the return after it.

    Home Assistant logs every failed refresh at ERROR, with no switch for it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        expected = record.levelno == logging.ERROR and any(
            isinstance(argument, ExpectedOutage) for argument in (record.args or ())
        )
        if expected:
            record.levelno = logging.INFO
            record.levelname = logging.getLevelName(logging.INFO)
        return True


_EXPECTED_OUTAGE_IS_NO_ERROR = _ExpectedOutageIsNoError()


def log_expected_outages_at_info(logger: logging.Logger) -> None:
    """Put the filter on the logger a coordinator was given.

    A filter on a parent logger never sees the record. The same filter is
    added once, however many entries there are.
    """
    logger.addFilter(_EXPECTED_OUTAGE_IS_NO_ERROR)
