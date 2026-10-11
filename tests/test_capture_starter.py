"""The capture's starter for Windows without Python: it runs only the Python it checked.

tools/webif_capture.bat downloads a portable Python on its first start and
runs tools/webif_capture.py with it. What it downloads runs on a stranger's
computer, so the check of the download is held by tests: they run the
PowerShell half of the file against a stand-in download on the loopback
address. They need PowerShell 7 (`pwsh`); a missing one fails rather than
skips, as for the scan tool.
"""

import functools
import hashlib
import http.server
import os
import pathlib
import re
import shutil
import subprocess
import threading
import zipfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
STARTER = REPO / "tools" / "webif_capture.bat"
CAPTURE = "webif_capture.py"
# What the stand-in Python does when run: note its arguments beside itself.
FAKE_PYTHON = "python.ps1"
FAKE_PYTHON_SOURCE = (
    "$args | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'called.txt')"
    " -Encoding utf8\n"
)
EMBEDDABLE = re.compile(
    r"https://www\.python\.org/ftp/python/(\d+\.\d+\.\d+)/"
    r"python-(\d+\.\d+\.\d+)-embed-amd64\.zip"
)


@pytest.fixture
def download(tmp_path, socket_enabled):
    """A stand-in python.org serving one zip; its URL, hash and request count."""
    served = tmp_path / "served"
    served.mkdir()
    archive = served / "python.zip"
    with zipfile.ZipFile(archive, "w") as packed:
        packed.writestr(FAKE_PYTHON, FAKE_PYTHON_SOURCE)
    requests = []

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            requests.append(self.path)

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(served))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "url": f"http://127.0.0.1:{server.server_address[1]}/python.zip",
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "requests": requests,
        }
    finally:
        server.shutdown()


@pytest.fixture
def tools(tmp_path):
    folder = tmp_path / "tools"
    folder.mkdir()
    (folder / CAPTURE).write_text("", encoding="utf-8")
    return folder


def _launcher_command() -> str:
    """The PowerShell command the batch half starts, as cmd hands it over."""
    source = STARTER.read_text(encoding="ascii")
    return re.search(r'^powershell .*-Command "(.*)"\r?$', source, re.MULTILINE).group(
        1
    )


def _start(tools, download, host="192.0.2.10", sha256=None):
    pwsh = shutil.which("pwsh")
    assert pwsh, "PowerShell 7 (pwsh) is needed to run the starter's tests"
    command = (
        f"{_launcher_command()} -ToolsDir '{tools}' -PythonUrl '{download['url']}'"
        f" -PythonSha256 '{sha256 or download['sha256']}' -PythonExe '{FAKE_PYTHON}'"
    )
    environment = {**os.environ, "CAPTURE_SELF": str(STARTER), "CAPTURE_HOST": host}
    return subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
        cwd=tools,
        check=False,
    )


def _called(tools) -> list[str] | None:
    noted = tools / "python" / "called.txt"
    if not noted.exists():
        return None
    return noted.read_text(encoding="utf-8-sig").splitlines()


def test_the_capture_runs_with_the_python_the_starter_checked(tools, download):
    process = _start(tools, download)

    assert process.returncode == 0, process.stdout + process.stderr
    assert _called(tools) == [str(tools / CAPTURE), "192.0.2.10"]
    assert {path.name for path in tools.iterdir()} == {CAPTURE, "python"}


def test_a_python_that_is_not_the_published_one_is_never_run(tools, download):
    """Whatever the download holds runs on a stranger's computer."""
    process = _start(tools, download, sha256="0" * 64)

    assert process.returncode != 0
    assert "nothing was run" in process.stdout
    assert {path.name for path in tools.iterdir()} == {CAPTURE}


def test_a_second_start_downloads_nothing(tools, download):
    _start(tools, download)

    process = _start(tools, download)

    assert process.returncode == 0, process.stdout + process.stderr
    assert len(download["requests"]) == 1


def test_the_address_reaches_the_capture_as_typed(tools, download):
    """Handed over as environment, never as source: an & or a quote in what
    the user typed stays text."""
    typed = 'pump & "x" $env:PATH'

    _start(tools, download, host=typed)

    assert _called(tools)[1] == typed


def test_without_the_capture_beside_it_nothing_is_downloaded(tools, download):
    """The batch file alone, without the repository, said nothing useful
    after an 11 MB download."""
    (tools / CAPTURE).unlink()

    process = _start(tools, download)

    assert process.returncode == 1
    assert "whole repository" in process.stdout
    assert download["requests"] == []


def test_the_starter_keeps_windows_line_ends():
    """cmd misreads a batch file with bare LF line ends, and Windows
    PowerShell 5.1 reads a file without BOM in the ANSI code page."""
    raw = STARTER.read_bytes()

    assert raw.isascii()
    assert raw.count(b"\n") == raw.count(b"\r\n") > 0


def test_the_pinned_python_is_an_embeddable_build_from_python_org():
    """URL and hash belong together: raising the version without the hash
    of its file leaves every first start refusing the download."""
    source = STARTER.read_text(encoding="ascii")
    url = re.search(r"\$PythonUrl = '([^']*)'", source).group(1)
    sha256 = re.search(r"\$PythonSha256 = '([^']*)'", source).group(1)

    version = EMBEDDABLE.fullmatch(url)
    assert version, url
    assert version.group(1) == version.group(2)
    assert re.fullmatch(r"[0-9a-f]{64}", sha256)
