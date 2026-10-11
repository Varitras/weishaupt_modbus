"""Counting the writes that reach the pump's EEPROM."""

from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

# The keys the counts are kept under between starts.
TOTAL = "total"
TODAY = "today"
DAY = "day"


class WriteBudget:
    """How many register writes went out, in total and today.

    A write counts once it may have reached the controller: answered, its
    answer lost or no longer waited for, or a connect that failed, which
    looks the same as a lost answer. One the controller refused does not.
    A value that is already active is skipped before it gets here. The day
    rolls over lazily on the next call.
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
        self._listeners: list[Callable[[], None]] = []

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

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` after every counted write; returns the unsubscribe."""
        self._listeners.append(listener)

        def remove() -> None:
            self._listeners.remove(listener)

        return remove

    def record_write(self) -> bool:
        """Count one write; True exactly when it reaches the warning threshold."""
        self._roll_day()
        self.total += 1
        self._today_count += 1
        for listener in list(self._listeners):
            listener()
        return self.warn_at > 0 and self._today_count == self.warn_at

    def counts(self) -> dict[str, Any]:
        """The counts as they are kept between starts."""
        self._roll_day()
        return {
            TOTAL: self.total,
            TODAY: self._today_count,
            DAY: self.day.isoformat(),
        }

    def load(self, counts: Mapping[str, Any]) -> None:
        """Take the counts kept from before; today's only if they are today's.

        Assigned, not added: they are loaded before anything can write.
        """
        self.total = int(counts.get(TOTAL) or 0)
        day = counts.get(DAY)
        if day and date.fromisoformat(day) == self._today():
            self.day = self._today()
            self._today_count = int(counts.get(TODAY) or 0)
