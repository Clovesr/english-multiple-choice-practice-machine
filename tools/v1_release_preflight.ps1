[CmdletBinding()]
param(
    [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$knownIssues = Join-Path $projectRoot "KNOWN_ISSUES.md"
$frontend = Join-Path $projectRoot "frontend"

function Invoke-CheckedStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )

    Write-Host "`n==> $Name"
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

Push-Location $projectRoot
try {
    if (-not $AllowDirty) {
        $dirty = @(git status --porcelain)
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect the Git working tree."
        }
        if ($dirty.Count -gt 0) {
            throw "Release preflight requires a clean working tree. Commit or stash changes first."
        }
    }

    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Python environment not found. Run setup.ps1 first."
    }
    if (-not (Test-Path -LiteralPath $knownIssues -PathType Leaf)) {
        throw "KNOWN_ISSUES.md is missing."
    }

    $issuesText = Get-Content -Raw -LiteralPath $knownIssues
    $unresolved = [regex]::Match(
        $issuesText,
        "(?s)## 未修问题(.*?)## 设计约束备忘"
    )
    if (-not $unresolved.Success) {
        throw "Unable to locate the unresolved-issues section."
    }
    if ($unresolved.Groups[1].Value -match "｜\s*P[01]\s*｜") {
        throw "Release gate blocked: unresolved P0/P1 issue found in KNOWN_ISSUES.md."
    }

    Invoke-CheckedStep "Git whitespace check" { git diff --check HEAD }
    Invoke-CheckedStep "Python dependency check" { & $python -m pip check }
    Invoke-CheckedStep "Backend bytecode compilation" { & $python -m compileall -q backend }
    Invoke-CheckedStep "Backend full regression" { & $python -m pytest tests -q }

    $corepack = Get-Command "corepack.cmd" -ErrorAction SilentlyContinue
    if (-not $corepack) {
        $corepack = Get-Command "corepack.exe" -ErrorAction SilentlyContinue
    }
    if (-not $corepack) {
        throw "Node.js with Corepack is required. Run setup.ps1 after installing Node.js."
    }

    Push-Location $frontend
    try {
        Invoke-CheckedStep "Frontend regression" { & $corepack.Source pnpm test }
        Invoke-CheckedStep "Frontend production build" { & $corepack.Source pnpm build }
    }
    finally {
        Pop-Location
    }

    $index = Join-Path $frontend "dist\index.html"
    if (-not (Test-Path -LiteralPath $index -PathType Leaf)) {
        throw "Frontend production artifact is missing: $index"
    }

    $commit = (git rev-parse --short HEAD).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to resolve the release commit."
    }
    Write-Host "`nV1 release preflight passed at commit $commit."
}
finally {
    Pop-Location
}
