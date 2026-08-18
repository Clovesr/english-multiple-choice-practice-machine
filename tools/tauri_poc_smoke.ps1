[CmdletBinding()]
param(
    [int]$AutoExitMilliseconds = 12000
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$executable = Join-Path $projectRoot "src-tauri\target\debug\wenqu-tauri-poc.exe"
$distIndex = Join-Path $projectRoot "frontend\dist\index.html"
$workDir = Join-Path $projectRoot "work"
$trace = Join-Path $workDir "tauri-poc-smoke-$([guid]::NewGuid().ToString('N')).jsonl"

if (-not (Test-Path -LiteralPath $distIndex -PathType Leaf)) {
    throw "Vue production distribution is missing: $distIndex"
}
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Tauri PoC executable is missing: $executable"
}
New-Item -ItemType Directory -Path $workDir -Force | Out-Null

$previousTrace = $env:WENQU_POC_TRACE_FILE
$previousExit = $env:WENQU_POC_AUTO_EXIT_MS
$first = $null
$second = $null
try {
    $env:WENQU_POC_TRACE_FILE = $trace
    $env:WENQU_POC_AUTO_EXIT_MS = [string]$AutoExitMilliseconds
    $first = Start-Process -FilePath $executable -PassThru -WindowStyle Hidden

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
    $events = @()
    do {
        Start-Sleep -Milliseconds 200
        if (Test-Path -LiteralPath $trace) {
            $events = @(Get-Content -LiteralPath $trace | ForEach-Object { $_ | ConvertFrom-Json })
        }
        if ($first.HasExited) {
            throw "First Tauri instance exited before loading the Vue distribution (exit $($first.ExitCode))."
        }
    } until (
        ($events | Where-Object event -eq "sidecar_ready") -and
        ($events | Where-Object event -eq "page_loaded") -or
        [DateTimeOffset]::UtcNow -ge $deadline
    )
    if (-not ($events | Where-Object event -eq "page_loaded")) {
        throw "Tauri WebView did not report a loaded page within 30 seconds."
    }

    $second = Start-Process -FilePath $executable -PassThru -WindowStyle Hidden
    if (-not $second.WaitForExit(5000)) {
        throw "Second Tauri launch did not hand off to the existing instance."
    }
    Start-Sleep -Milliseconds 500
    $events = @(Get-Content -LiteralPath $trace | ForEach-Object { $_ | ConvertFrom-Json })
    $readyEvents = @($events | Where-Object event -eq "sidecar_ready")
    if ($readyEvents.Count -ne 1) {
        throw "Single-instance check started $($readyEvents.Count) sidecars; expected exactly one."
    }

    if (-not $first.WaitForExit($AutoExitMilliseconds + 10000)) {
        throw "First Tauri instance did not exit within the smoke-test deadline."
    }
    Start-Sleep -Milliseconds 500
    $events = @(Get-Content -LiteralPath $trace | ForEach-Object { $_ | ConvertFrom-Json })
    $ready = $events | Where-Object event -eq "sidecar_ready" | Select-Object -First 1
    if (Get-Process -Id $ready.pid -ErrorAction SilentlyContinue) {
        throw "FastAPI sidecar PID $($ready.pid) remained after the Tauri process exited."
    }
    if (-not ($events | Where-Object event -eq "sidecar_stopped")) {
        throw "Tauri cleanup did not record sidecar shutdown."
    }

    [pscustomobject]@{
        Result = "PASS"
        TauriPid = $first.Id
        SidecarPid = $ready.pid
        RandomPort = $ready.port
        PageLoads = @($events | Where-Object event -eq "page_loaded").Count
        SidecarsStarted = $readyEvents.Count
        ResidualSidecar = $false
        Trace = $trace
    } | Format-List
}
finally {
    foreach ($process in @($second, $first)) {
        if ($null -ne $process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    $env:WENQU_POC_TRACE_FILE = $previousTrace
    $env:WENQU_POC_AUTO_EXIT_MS = $previousExit
}
