<# : cmd runs the lines up to "exit /b"; PowerShell reads them as a comment.
@echo off
setlocal
set "CAPTURE_HOST=%~1"
if not defined CAPTURE_HOST set /p "CAPTURE_HOST=Address of the heat pump: "
set "CAPTURE_SELF=%~f0"
rem Path and host reach PowerShell as environment, never as source text.
powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((Get-Content -LiteralPath $env:CAPTURE_SELF -Raw))) -HostName $env:CAPTURE_HOST"
pause
exit /b

Records the titles a heat pump's web interface shows, without an installed
Python: it runs webif_capture.py next to it with a portable Python.

Double-click and enter the heat pump's address, or run
    webif_capture.bat 192.0.2.10
from a copy of the whole repository (Code -> Download ZIP, unpacked).

On the first start it downloads the official embeddable Python from
python.org (about 11 MB) into the folder "python" next to this file, checks
its SHA-256 and unpacks it; later starts use it again. Nothing is installed
and the portable Python stays in this folder: deleting "python" removes it.
The capture asks for the web interface's user and password, and the file it
writes lands in the current working directory.
#>
param(
    [Parameter(Mandatory = $true)][string]$HostName,
    [string]$ToolsDir = (Split-Path -Parent $env:CAPTURE_SELF),
    [string]$PythonUrl = 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip',
    # As python.org publishes it for that file.
    [string]$PythonSha256 = '97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297',
    [string]$PythonExe = 'python.exe'
)

$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 draws its progress bar for every block received,
# which slows a download many times over.
$ProgressPreference = 'SilentlyContinue'

$capture = Join-Path $ToolsDir 'webif_capture.py'
$pythonDir = Join-Path $ToolsDir 'python'
$python = Join-Path $pythonDir $PythonExe

function Install-Python {
    $download = Join-Path $ToolsDir 'python-download.zip'
    $unpacking = Join-Path $ToolsDir 'python-unpacking'
    Write-Host 'Downloading Python from python.org, once (about 11 MB) ...'
    # Windows PowerShell 5.1 may offer only TLS versions python.org refuses.
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $PythonUrl -OutFile $download -UseBasicParsing
    $hash = (Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash
    if ($hash -ne $PythonSha256) {
        Remove-Item -LiteralPath $download
        Write-Host "The download is not the Python it should be (SHA-256 $hash); nothing was run."
        return $false
    }
    foreach ($folder in @($unpacking, $pythonDir)) {
        if (Test-Path -LiteralPath $folder) {
            Remove-Item -LiteralPath $folder -Recurse
        }
    }
    Expand-Archive -LiteralPath $download -DestinationPath $unpacking
    Remove-Item -LiteralPath $download
    # Unpacked aside and moved at once: a start broken off midway leaves no
    # half Python that the next start would take for a whole one.
    Move-Item -LiteralPath $unpacking -Destination $pythonDir
    return $true
}

if (-not (Test-Path -LiteralPath $capture)) {
    Write-Host 'webif_capture.py is missing next to this file: run it from a copy of the whole repository (Code -> Download ZIP, unpacked).'
    exit 1
}
if (-not (Test-Path -LiteralPath $python)) {
    if (-not (Install-Python)) {
        exit 1
    }
}
& $python $capture $HostName
exit $LASTEXITCODE
