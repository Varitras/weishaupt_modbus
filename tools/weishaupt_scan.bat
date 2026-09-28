<# : cmd runs the lines up to "exit /b"; PowerShell reads them as a comment.
@echo off
setlocal
set "SCAN_HOST=%~1"
if "%SCAN_HOST%"=="" set /p "SCAN_HOST=IP address of the heat pump: "
powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((Get-Content -LiteralPath '%~f0' -Raw))) -HostName '%SCAN_HOST%'"
pause
exit /b

Reads the Modbus registers of a Weishaupt heat pump and saves them as CSV.

Double-click and enter the pump's IP address, or run
    weishaupt_scan.bat 192.168.1.50
The CSV lands in the current folder.

Asks for every input register (30001-39999) and holding register
(40001-49999) one at a time. The CSV lists each register the pump
answers, and each address it answers with an error other than
"illegal data address", which marks a register that exists but
cannot be read right now. It also asks for the Modbus device
identification, which not every controller supports.

Read-only: the script sends only function codes 0x03, 0x04 and 0x2B,
never a write. The CSV holds no address of the pump.

The pump serves one request at a time; while the scan runs, Home
Assistant may miss a poll or two. The scan takes about ten minutes.
#>
param(
    [Parameter(Mandatory = $true)][string]$HostName,
    [int]$Port = 502,
    [byte]$UnitId = 1,
    [int[]]$InputRegisters = (30001..39999),
    [int[]]$HoldingRegisters = (40001..49999),
    [string]$OutFile = ("weishaupt_scan_{0:yyyyMMdd_HHmmss}.csv" -f (Get-Date)),
    [int]$TimeoutMs = 2000
)

Set-StrictMode -Version 3
$ErrorActionPreference = 'Stop'

$FunctionReadHolding = 0x03
$FunctionReadInput = 0x04
$FunctionEncapsulated = 0x2B
$MeiReadDeviceId = 0x0E
$DeviceIdBasic = 1
$ExceptionFlag = 0x80
$IllegalDataAddress = 2
# A pump that drops this many requests in a row is gone, not slow.
$MaxTimeoutsInRow = 5

$script:client = $null
$script:stream = $null
$script:transactionId = 0

function Connect-Pump {
    if ($script:client) { $script:client.Close() }
    $script:client = New-Object System.Net.Sockets.TcpClient
    $pending = $script:client.BeginConnect($HostName, $Port, $null, $null)
    if (-not $pending.AsyncWaitHandle.WaitOne($TimeoutMs)) {
        throw "No answer from ${HostName}:$Port - check the address and that Modbus TCP is enabled."
    }
    $script:client.EndConnect($pending)
    $script:stream = $script:client.GetStream()
    $script:stream.ReadTimeout = $TimeoutMs
    $script:stream.WriteTimeout = $TimeoutMs
}

function Read-Exactly([int]$count) {
    $buffer = New-Object byte[] $count
    $offset = 0
    while ($offset -lt $count) {
        $read = $script:stream.Read($buffer, $offset, $count - $offset)
        if ($read -eq 0) { throw [System.IO.IOException]::new('The pump closed the connection.') }
        $offset += $read
    }
    return , $buffer
}

function Invoke-Pdu([byte[]]$pdu) {
    $script:transactionId = ($script:transactionId + 1) -band 0xFFFF
    $length = $pdu.Length + 1
    $frame = [byte[]](@(
            ($script:transactionId -shr 8), ($script:transactionId -band 0xFF),
            0, 0,
            ($length -shr 8), ($length -band 0xFF),
            $UnitId) + $pdu)
    $script:stream.Write($frame, 0, $frame.Length)
    $header = Read-Exactly 7
    $body = Read-Exactly ($header[4] * 256 + $header[5] - 1)
    # A late answer to a timed-out request would shift every value after it.
    if (($header[0] * 256 + $header[1]) -ne $script:transactionId) {
        throw [System.IO.IOException]::new('The pump answered an earlier request.')
    }
    return , $body
}

function Read-Register([int]$function, [int]$address) {
    $body = Invoke-Pdu ([byte[]]@($function, ($address -shr 8), ($address -band 0xFF), 0, 1))
    if ($body[0] -band $ExceptionFlag) { return @{ Exception = [int]$body[1] } }
    return @{ Value = $body[2] * 256 + $body[3] }
}

function Read-DeviceIdentification {
    $body = Invoke-Pdu ([byte[]]@($FunctionEncapsulated, $MeiReadDeviceId, $DeviceIdBasic, 0))
    if ($body[0] -band $ExceptionFlag) {
        return [pscustomobject]@{ table = 'device_id'; address = ''; value = ''; note = "exception $($body[1])" }
    }
    # fc, MEI type, id code, conformity, more follows, next id, object count, objects
    $offset = 7
    for ($index = 0; $index -lt $body[6]; $index++) {
        $length = $body[$offset + 1]
        [pscustomobject]@{
            table   = 'device_id'
            address = $body[$offset]
            value   = ''
            note    = [System.Text.Encoding]::ASCII.GetString($body, $offset + 2, $length)
        }
        $offset += 2 + $length
    }
}

function Read-Table([string]$table, [int]$function, [int[]]$addresses) {
    $timeoutsInRow = 0
    $done = 0
    foreach ($address in $addresses) {
        $done++
        if ($done % 100 -eq 0) {
            Write-Progress -Activity "Reading $table registers" -Status "$address" `
                -PercentComplete (100 * $done / $addresses.Count)
        }
        try {
            $answer = Read-Register $function $address
            $timeoutsInRow = 0
        }
        catch [System.IO.IOException] {
            $timeoutsInRow++
            if ($timeoutsInRow -ge $MaxTimeoutsInRow) {
                throw "The pump stopped answering at $table register $address."
            }
            [pscustomobject]@{ table = $table; address = $address; value = ''; note = $_.Exception.Message }
            Connect-Pump
            continue
        }
        if ($answer.ContainsKey('Value')) {
            [pscustomobject]@{ table = $table; address = $address; value = $answer.Value; note = '' }
        }
        elseif ($answer.Exception -ne $IllegalDataAddress) {
            [pscustomobject]@{ table = $table; address = $address; value = ''; note = "exception $($answer.Exception)" }
        }
    }
    Write-Progress -Activity "Reading $table registers" -Completed
}

Connect-Pump
$rows = @()
try {
    try { $rows += @(Read-DeviceIdentification) }
    catch [System.IO.IOException] { $rows += [pscustomobject]@{ table = 'device_id'; address = ''; value = ''; note = $_.Exception.Message } }
    # A WBB answers the unknown function 0x2B and then drops the connection.
    Connect-Pump
    $rows += @(Read-Table 'input' $FunctionReadInput $InputRegisters)
    $rows += @(Read-Table 'holding' $FunctionReadHolding $HoldingRegisters)
}
finally {
    if ($rows.Count -gt 0) { $rows | Export-Csv -Path $OutFile -NoTypeInformation -Encoding UTF8 }
    $script:client.Close()
}

$answered = @($rows | Where-Object { $_.table -ne 'device_id' -and $_.value -ne '' }).Count
Write-Host "$answered registers answered, written to $OutFile"
