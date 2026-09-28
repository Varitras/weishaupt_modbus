"""The register scan users run for a support request never writes to the pump.

tools/weishaupt_scan.bat is handed to people with a heat pump the
integration does not know yet. A write there lands in the controller's
EEPROM, on a pump nobody here has seen - so the promise "read-only" is held
by a test, not by a comment.
"""

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
SCAN_TOOL = REPO / "tools" / "weishaupt_scan.bat"

# Read holding registers, read input registers, read device identification.
READ_FUNCTION_CODES = {0x03, 0x04, 0x2B}


def test_the_scan_tool_never_writes():
    source = SCAN_TOOL.read_text(encoding="ascii")
    codes = {
        name: int(value, 16)
        for name, value in re.findall(r"\$(Function\w+) = (0x[0-9A-Fa-f]+)", source)
    }
    first_bytes = re.findall(r"Invoke-Pdu \(\[byte\[\]\]@\(([^,]+),", source)

    assert codes, "no function code constants found - the scan changed shape"
    assert first_bytes, "no request found - the scan changed shape"
    assert set(codes.values()) <= READ_FUNCTION_CODES, codes
    for first in first_bytes:
        # A request built from a literal or an unnamed variable escapes the
        # check above; only the named read codes may open a request.
        assert first.lstrip("$") in codes or first == "$function", first
    for name in re.findall(r"Read-Table '\w+' \$(\w+)", source):
        assert name in codes, name


def test_the_scan_tool_keeps_windows_line_ends():
    """cmd misreads a batch file with bare LF line ends, and Windows
    PowerShell 5.1 reads a file without BOM in the ANSI code page - so CRLF
    and plain ASCII, byte for byte, including a download of the raw file."""
    raw = SCAN_TOOL.read_bytes()

    assert raw.isascii()
    assert raw.count(b"\n") == raw.count(b"\r\n") > 0
