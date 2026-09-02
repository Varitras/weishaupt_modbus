"""Counting the writes that reach the pump's EEPROM."""

from collections.abc import Callable
from datetime import date


class WriteBudget:
    """How many register writes went out, in total and today.

    Only writes that reach the wire count; a value that is already active is
    skipped before it gets here. The day rolls over lazily on the next call.
    """

    def __init__(
        self,
        warn_at: int,
        limit: int,
        today: Callable[[], date] = date.today,
    ) -> None:
        """Thresholds of 0 switch the warning and the limit off."""
        self.warn_at = warn_at
        self.limit = limit
        self._today = today
        self.total = 0
        self._today_count = 0
        self.day = today()

    def _roll_day(self) -> None:
        if self.day == self._today():
            return
        self.day = self._today()
        self._today_count = 0

    @property
    def writes_today(self) -> int:
        """Writes since local midnight."""
        self._roll_day()
        return self._today_count

    def allows_write(self) -> bool:
        """False once today's limit is used up."""
        self._roll_day()
        return self.limit == 0 or self._today_count < self.limit

    def record_write(self) -> bool:
        """Count one write; True exactly when it reaches the warning threshold."""
        self._roll_day()
        self.total += 1
        self._today_count += 1
        return self.warn_at > 0 and self._today_count == self.warn_at

    def restore_total(self, total: int) -> None:
        """The lifetime count as the sensor last recorded it."""
        self.total = total

    def restore_today(self, count: int, day: date) -> None:
        """A count from a past day is stale and stays at zero."""
        if day == self._today():
            self.day = day
            self._today_count = count
