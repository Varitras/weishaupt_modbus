"""Errors the device library raises on its own account."""

from modbus_connection import ModbusError


class SystemBandRefused(ModbusError):
    """The controller refused the system registers every Weishaupt serves.

    A ModbusError, so the coordinator treats it as a failed refresh: a
    device that answers but refuses 30001-30006 is not the heat pump, and
    "every band absent" must not read as a healthy poll with no values.
    """


class LayoutUndetermined(ModbusError):
    """The 2nd heat source registers answered like neither known layout.

    A ModbusError, so the setup treats it as a communication failure and
    retries: guessing could write a temperature into a configuration register.
    """


class WriteError(Exception):
    """A register write was refused before it reached the wire: the day's limit."""

    def __init__(self, limit: int, address: int) -> None:
        """Keep the limit for the message the user gets in their language."""
        super().__init__(
            f"Daily write limit of {limit} reached; register {address} not written"
        )
        self.limit = limit
