"""Errors the device library raises on its own account."""


class WriteError(Exception):
    """A register write was refused before it reached the wire."""
