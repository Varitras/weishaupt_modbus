"""The register table's row types."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StatusItem:
    """One value a status register can hold, and the key its text lives under."""

    number: int
    text: str
    translation_key: str = ""
    # Service advice on the error codes: documentation in the table, never read.
    description: str = ""


@dataclass
class ModbusItem:
    """One register of the table, plus what the last poll read for it."""

    address: int
    name: str
    format: str
    type: str
    device: str
    translation_key: str
    resultlist: list[StatusItem] | None = None
    params: dict[str, Any] = field(default_factory=dict)
    batch: int | None = None
    # Runtime state, written by the client and the coordinator.
    state: Any = field(default=None, init=False)
    is_invalid: bool = field(default=False, init=False)

    def _status(self, number: int | None) -> StatusItem | None:
        if number is None or self.resultlist is None:
            return None
        return next((s for s in self.resultlist if s.number == number), None)

    def get_text_from_number(self, val: int | None) -> str | None:
        """The status text for a number; unknown numbers name themselves."""
        if val is None or self.resultlist is None:
            return None
        status = self._status(val)
        return status.text if status else f"unbekannt <{val}>"

    def get_translation_key_from_number(self, val: int | None) -> str | None:
        """The state translation key for a number; unknown numbers name themselves."""
        if val is None or self.resultlist is None:
            return None
        status = self._status(val)
        return status.translation_key if status else f"unbekannt <{val}>"

    def get_number_from_text(self, val: str) -> int | None:
        """The number behind a status text, None for a text that is not one."""
        if self.resultlist is None:
            return None
        return next((s.number for s in self.resultlist if s.text == val), None)

    def get_number_from_translation_key(self, val: str | None) -> int | None:
        """The number behind a state key, None for a key that is not one."""
        if val is None or self.resultlist is None:
            return None
        return next(
            (s.number for s in self.resultlist if s.translation_key == val), None
        )
