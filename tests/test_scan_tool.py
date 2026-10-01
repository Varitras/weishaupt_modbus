"""The register scan users run for a support request never writes to the pump.

tools/weishaupt_scan.bat is handed to people with a heat pump the
integration does not know yet. A write there lands in the controller's
EEPROM, on a pump nobody here has seen - so the promise "read-only" is held
by a test, not by a comment.

The behaviour tests run the PowerShell half of the file against a stand-in
Modbus server on the loopback address and look at what actually arrives.
They need PowerShell 7 (`pwsh`); GitHub's Ubuntu runners ship it, and a
missing one fails rather than skips - a skipped safety test reads as a pass.
"""

import csv
import os
import pathlib
import re
import shutil
import socketserver
import subprocess
import threading

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCAN_TOOL = REPO / "tools" / "weishaupt_scan.bat"

# Read holding registers, read input registers, read device identification.
READ_FUNCTION_CODES = {0x03, 0x04, 0x2B}
READ_HOLDING = 0x03
READ_INPUT = 0x04
ENCAPSULATED = 0x2B
EXCEPTION_FLAG = 0x80
ILLEGAL_FUNCTION = 1
ILLEGAL_DATA_ADDRESS = 2
MORE_FOLLOWS = 0xFF
MBAP_LENGTH = 7


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


class _StandInPump:
    """Answers like a heat pump: known registers, illegal address otherwise."""

    def __init__(self, registers=None, drop=(), identification=None):
        self.registers = registers or {}
        # Addresses whose request closes the connection instead of an answer.
        self.drop = set(drop)
        # First object id of a page -> (objects, next object id or None).
        self.identification = identification
        self.functions: list[int] = []

    def answer(self, pdu: bytes) -> bytes | None:
        function = pdu[0]
        self.functions.append(function)
        if function in (READ_HOLDING, READ_INPUT):
            address = int.from_bytes(pdu[1:3], "big")
            if address in self.drop:
                return None
            value = self.registers.get((function, address))
            if value is None:
                return bytes([function | EXCEPTION_FLAG, ILLEGAL_DATA_ADDRESS])
            return bytes([function, 2]) + value.to_bytes(2, "big")
        if function == ENCAPSULATED and self.identification:
            return self._identification_page(pdu)
        return bytes([function | EXCEPTION_FLAG, ILLEGAL_FUNCTION])

    def _identification_page(self, pdu: bytes) -> bytes:
        objects, next_id = self.identification[pdu[3]]
        more = MORE_FOLLOWS if next_id is not None else 0
        page = bytes([ENCAPSULATED, pdu[1], pdu[2], 1, more, next_id or 0])
        page += bytes([len(objects)])
        for object_id, text in objects:
            page += bytes([object_id, len(text)]) + text.encode("ascii")
        return page


def _receive(connection, count: int) -> bytes | None:
    data = b""
    while len(data) < count:
        chunk = connection.recv(count - len(data))
        if not chunk:
            return None
        data += chunk
    return data


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        while header := _receive(self.request, MBAP_LENGTH):
            pdu = _receive(self.request, int.from_bytes(header[4:6], "big") - 1)
            reply = self.server.pump.answer(pdu) if pdu else None
            if reply is None:
                return
            length = (len(reply) + 1).to_bytes(2, "big")
            self.request.sendall(header[:4] + length + header[6:7] + reply)


@pytest.fixture
def serve(socket_enabled):
    """Start a stand-in pump on the loopback address; yields a starter."""
    servers = []

    def start(pump: _StandInPump) -> int:
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        server.pump = pump
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_address[1]

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _launcher_command() -> str:
    """The PowerShell command the batch half starts, as cmd hands it over."""
    source = SCAN_TOOL.read_text(encoding="ascii")
    return re.search(r'^powershell .*-Command "(.*)"\r?$', source, re.MULTILINE).group(
        1
    )


def _scan(tmp_path, port, host="127.0.0.1", tool=SCAN_TOOL, **ranges):
    """Run the scan the way the launcher does; returns (process, CSV rows)."""
    pwsh = shutil.which("pwsh")
    assert pwsh, "PowerShell 7 (pwsh) is needed to run the scan tool's tests"
    out_file = tmp_path / "scan.csv"
    arguments = " ".join(
        f"-{name} @({','.join(map(str, addresses))})"
        for name, addresses in ranges.items()
    )
    command = f"{_launcher_command()} -Port {port} {arguments} -OutFile '{out_file}'"
    environment = {**os.environ, "SCAN_SELF": str(tool), "SCAN_HOST": host}
    process = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
        cwd=tmp_path,
        check=False,
    )
    rows = []
    if out_file.exists():
        with out_file.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    return process, rows


def _values(rows, table):
    return {
        int(row["address"]): int(row["value"])
        for row in rows
        if row["table"] == table and row["value"]
    }


def test_only_read_requests_reach_the_pump(tmp_path, serve):
    pump = _StandInPump(registers={(READ_INPUT, 30002): 195, (READ_HOLDING, 41101): 3})
    port = serve(pump)

    process, rows = _scan(
        tmp_path,
        port,
        InputRegisters=[30001, 30002],
        HoldingRegisters=[41101, 41102],
    )

    assert process.returncode == 0, process.stderr
    assert set(pump.functions) <= READ_FUNCTION_CODES, pump.functions
    assert _values(rows, "input") == {30002: 195}
    assert _values(rows, "holding") == {41101: 3}


def test_a_register_reading_zero_counts_as_answered(tmp_path, serve):
    """PowerShell's `0 -ne ''` is false: the closing line counted 23 of 41
    answered registers on a real pump, every one reading 0 left out."""
    pump = _StandInPump(registers={(READ_INPUT, 30001): 0, (READ_INPUT, 30002): 195})
    port = serve(pump)

    process, rows = _scan(
        tmp_path, port, InputRegisters=[30001, 30002], HoldingRegisters=[]
    )

    assert process.returncode == 0, process.stderr
    assert _values(rows, "input") == {30001: 0, 30002: 195}
    assert "2 registers answered" in process.stdout, process.stdout


def test_an_aborted_scan_keeps_the_registers_it_read(tmp_path, serve):
    """Five dropped requests in a row end the scan; what came before is
    evidence and was lost with the table it belonged to."""
    pump = _StandInPump(
        registers={
            (READ_INPUT, address): address - 30000 for address in (30001, 30002, 30003)
        },
        drop=range(30004, 30009),
    )
    port = serve(pump)

    process, rows = _scan(
        tmp_path,
        port,
        InputRegisters=list(range(30001, 30011)),
        HoldingRegisters=[],
    )

    assert process.returncode != 0, "the scan should stop at the dead link"
    assert _values(rows, "input") == {30001: 1, 30002: 2, 30003: 3}


def test_a_device_identification_over_two_pages_is_read_to_the_end(tmp_path, serve):
    """A controller whose identification does not fit one answer says
    "more follows" and names the next object; the second page was dropped."""
    pump = _StandInPump(
        identification={
            0: ([(0, "Vendor")], 1),
            1: ([(1, "Product"), (2, "1.2.3")], None),
        }
    )
    port = serve(pump)

    process, rows = _scan(tmp_path, port, InputRegisters=[], HoldingRegisters=[])

    assert process.returncode == 0, process.stderr
    identification = {
        row["address"]: row["note"] for row in rows if row["table"] == "device_id"
    }
    assert identification == {"0": "Vendor", "1": "Product", "2": "1.2.3"}


def test_the_launcher_hands_over_path_and_host_as_data(tmp_path, serve):
    """cmd expanded the path and the typed host into PowerShell source: an
    apostrophe in a folder name stopped the tool, and one in the host ran
    whatever followed it."""
    assert "%" not in _launcher_command(), "cmd would expand into the command"
    folder = tmp_path / "Scan User's Folder"
    folder.mkdir()
    tool = folder / SCAN_TOOL.name
    shutil.copyfile(SCAN_TOOL, tool)
    pump = _StandInPump(registers={(READ_INPUT, 30002): 195})
    port = serve(pump)

    process, rows = _scan(
        tmp_path, port, tool=tool, InputRegisters=[30002], HoldingRegisters=[]
    )
    assert process.returncode == 0, process.stderr
    assert _values(rows, "input") == {30002: 195}

    marker = tmp_path / "injected"
    _scan(
        tmp_path,
        port,
        host=f"127.0.0.1'; New-Item '{marker}'; #",
        tool=tool,
        InputRegisters=[30002],
        HoldingRegisters=[],
    )
    assert not marker.exists(), "the typed host ran as code"
