"""Errors the device library raises on its own account."""

from modbus_connection import ModbusError


class SystemBandRefused(ModbusError):
    """The controller refused the system registers every Weishaupt serves.

    A ModbusError, so the coordinator treats it as a failed refresh: a
    device that answers but refuses 30001-30006 is not the heat pump, and
    "every band absent" must not read as a healthy poll with no values.
    """


class WriteError(Exception):
    """A register write was refused before it reached the wire."""
