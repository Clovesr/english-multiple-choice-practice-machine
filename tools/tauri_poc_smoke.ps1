[CmdletBinding()]
param(
    [int]$AutoExitMilliseconds = 12000,
    [int]$RecoveryExitMilliseconds = 4000,
    [ValidateRange(1, 20)]
    [int]$SecondaryLaunches = 9
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$executable = Join-Path $projectRoot "src-tauri\target\debug\wenqu-tauri-poc.exe"
$distIndex = Join-Path $projectRoot "frontend\dist\index.html"
$workDir = Join-Path $projectRoot "work"
$trace = Join-Path $workDir "tauri-poc-smoke-$([guid]::NewGuid().ToString('N')).jsonl"
$allTauriProcesses = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

if (-not (Test-Path -LiteralPath $distIndex -PathType Leaf)) {
    throw "Vue production distribution is missing: $distIndex"
}
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Tauri PoC executable is missing: $executable"
}
New-Item -ItemType Directory -Path $workDir -Force | Out-Null

function Read-TraceEvents {
    if (-not (Test-Path -LiteralPath $trace -PathType Leaf)) {
        return @()
    }
    return @(
        Get-Content -LiteralPath $trace |
            Where-Object { $_.Trim() } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
}

function Start-PocInstance([int]$ExitMilliseconds) {
    $env:WENQU_POC_AUTO_EXIT_MS = [string]$ExitMilliseconds
    $process = Start-Process -FilePath $executable -PassThru -WindowStyle Hidden
    $allTauriProcesses.Add($process)
    return $process
}

function Wait-ForEventCount(
    [string]$EventName,
    [int]$ExpectedCount,
    [int]$TimeoutSeconds,
    [System.Diagnostics.Process]$GuardProcess,
    [switch]$RequireFinalPage
) {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 200
        $events = Read-TraceEvents
        $matches = @($events | Where-Object {
            $_.event -eq $EventName -and
            (-not $RequireFinalPage -or $_.url -notlike "*/desktop/bootstrap*")
        })
        if ($matches.Count -ge $ExpectedCount) {
            return $events
        }
        if ($null -ne $GuardProcess) {
            $GuardProcess.Refresh()
            if ($GuardProcess.HasExited) {
                throw "Tauri instance $($GuardProcess.Id) exited before $EventName reached $ExpectedCount."
            }
        }
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Trace event $EventName did not reach $ExpectedCount within $TimeoutSeconds seconds."
}

function Wait-ForProcessGone([int]$ProcessId, [int]$TimeoutSeconds) {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            return
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Process $ProcessId remained alive for more than $TimeoutSeconds seconds."
}

function Assert-CycleClean([int]$ReadyIndex, [switch]$ExpectGracefulTrace) {
    $events = Read-TraceEvents
    $readyEvents = @($events | Where-Object event -eq "sidecar_ready")
    if ($readyEvents.Count -le $ReadyIndex) {
        throw "No sidecar_ready event exists at index $ReadyIndex."
    }
    $ready = $readyEvents[$ReadyIndex]
    Wait-ForProcessGone -ProcessId $ready.pid -TimeoutSeconds 10
    if ($ExpectGracefulTrace -and -not ($events | Where-Object {
        $_.event -eq "sidecar_stopped" -and $_.pid -eq $ready.pid
    })) {
        throw "Graceful shutdown for sidecar PID $($ready.pid) was not traced."
    }
}

$previousTrace = $env:WENQU_POC_TRACE_FILE
$previousExit = $env:WENQU_POC_AUTO_EXIT_MS
try {
    $env:WENQU_POC_TRACE_FILE = $trace

    # APP-01/02: load the production Vue distribution through the authenticated sidecar.
    $first = Start-PocInstance -ExitMilliseconds $AutoExitMilliseconds
    Wait-ForEventCount -EventName "sidecar_ready" -ExpectedCount 1 -TimeoutSeconds 30 -GuardProcess $first | Out-Null
    Wait-ForEventCount -EventName "page_loaded" -ExpectedCount 1 -TimeoutSeconds 30 -GuardProcess $first -RequireFinalPage | Out-Null

    # APP-04: ten launches total must retain one primary and focus it on every handoff.
    for ($index = 0; $index -lt $SecondaryLaunches; $index += 1) {
        $secondary = Start-PocInstance -ExitMilliseconds $AutoExitMilliseconds
        if (-not $secondary.WaitForExit(5000)) {
            throw "Secondary Tauri launch $($secondary.Id) did not hand off within five seconds."
        }
    }
    Wait-ForEventCount -EventName "single_instance_handoff" -ExpectedCount $SecondaryLaunches -TimeoutSeconds 10 -GuardProcess $first | Out-Null
    $events = Read-TraceEvents
    if (@($events | Where-Object event -eq "sidecar_ready").Count -ne 1) {
        throw "Single-instance launches created more than one sidecar."
    }
    if (-not $first.WaitForExit($AutoExitMilliseconds + 10000)) {
        throw "Primary Tauri instance did not auto-exit within the smoke-test deadline."
    }
    Assert-CycleClean -ReadyIndex 0 -ExpectGracefulTrace

    # A clean restart proves the single-instance IPC endpoint was released.
    $cleanRestart = Start-PocInstance -ExitMilliseconds $RecoveryExitMilliseconds
    Wait-ForEventCount -EventName "sidecar_ready" -ExpectedCount 2 -TimeoutSeconds 30 -GuardProcess $cleanRestart | Out-Null
    Wait-ForEventCount -EventName "page_loaded" -ExpectedCount 2 -TimeoutSeconds 30 -GuardProcess $cleanRestart -RequireFinalPage | Out-Null
    if (-not $cleanRestart.WaitForExit($RecoveryExitMilliseconds + 10000)) {
        throw "Clean-restart Tauri instance did not exit."
    }
    Assert-CycleClean -ReadyIndex 1 -ExpectGracefulTrace

    # APP-02 failure path: force-killing the shell closes stdin and the sidecar self-terminates.
    $forced = Start-PocInstance -ExitMilliseconds 60000
    Wait-ForEventCount -EventName "sidecar_ready" -ExpectedCount 3 -TimeoutSeconds 30 -GuardProcess $forced | Out-Null
    Wait-ForEventCount -EventName "page_loaded" -ExpectedCount 3 -TimeoutSeconds 30 -GuardProcess $forced -RequireFinalPage | Out-Null
    Stop-Process -Id $forced.Id -Force
    $forced.WaitForExit(5000) | Out-Null
    Assert-CycleClean -ReadyIndex 2

    # APP-04 stale-lock recovery: a new primary must start after the forced exit.
    $staleLockRestart = Start-PocInstance -ExitMilliseconds $RecoveryExitMilliseconds
    Wait-ForEventCount -EventName "sidecar_ready" -ExpectedCount 4 -TimeoutSeconds 30 -GuardProcess $staleLockRestart | Out-Null
    Wait-ForEventCount -EventName "page_loaded" -ExpectedCount 4 -TimeoutSeconds 30 -GuardProcess $staleLockRestart -RequireFinalPage | Out-Null
    if (-not $staleLockRestart.WaitForExit($RecoveryExitMilliseconds + 10000)) {
        throw "Stale-lock recovery instance did not exit."
    }
    Assert-CycleClean -ReadyIndex 3 -ExpectGracefulTrace

    $events = Read-TraceEvents
    $readyEvents = @($events | Where-Object event -eq "sidecar_ready")
    $traceText = Get-Content -LiteralPath $trace -Raw
    if ($traceText -match 'token=[0-9a-f]{64}') {
        throw "Desktop session token leaked into the PoC trace."
    }

    [pscustomobject]@{
        Result = "PASS"
        LaunchesInSingleInstanceBurst = 1 + $SecondaryLaunches
        SingleInstanceHandoffs = @($events | Where-Object event -eq "single_instance_handoff").Count
        SidecarPids = ($readyEvents.pid -join ", ")
        RandomLoopbackPorts = ($readyEvents.port -join ", ")
        GracefulStops = @($events | Where-Object event -eq "sidecar_stopped").Count
        ForcedParentStopRecovered = $true
        StaleLockRecovered = $true
        ResidualSidecars = $false
        Trace = $trace
    } | Format-List
}
finally {
    foreach ($process in $allTauriProcesses) {
        $process.Refresh()
        if (-not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    foreach ($ready in @(Read-TraceEvents | Where-Object event -eq "sidecar_ready")) {
        if (Get-Process -Id $ready.pid -ErrorAction SilentlyContinue) {
            Stop-Process -Id $ready.pid -Force -ErrorAction SilentlyContinue
        }
    }
    $env:WENQU_POC_TRACE_FILE = $previousTrace
    $env:WENQU_POC_AUTO_EXIT_MS = $previousExit
}
