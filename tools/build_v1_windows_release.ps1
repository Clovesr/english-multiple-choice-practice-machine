[CmdletBinding()]
param(
    [ValidatePattern("^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    [string]$Version = "v1-core",
    [switch]$Overwrite
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$preflight = Join-Path $PSScriptRoot "v1_release_preflight.ps1"
$frontendDist = Join-Path $projectRoot "frontend\dist"
$outputDir = Join-Path $projectRoot "outputs\releases"

function Invoke-CheckedNative {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )

    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

Push-Location $projectRoot
try {
    $dirty = @(git status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect the Git working tree."
    }
    if ($dirty.Count -gt 0) {
        throw "Release packaging requires a clean working tree."
    }

    if (-not (Test-Path -LiteralPath $preflight -PathType Leaf)) {
        throw "Release preflight script is missing: $preflight"
    }
    & $preflight

    $commit = (git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to resolve the release commit."
    }
    $shortCommit = (git rev-parse --short=7 HEAD).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to resolve the short release commit."
    }

    $index = Join-Path $frontendDist "index.html"
    if (-not (Test-Path -LiteralPath $index -PathType Leaf)) {
        throw "Production frontend is missing. Run the release preflight first."
    }

    $distFiles = @(Get-ChildItem -LiteralPath $frontendDist -Recurse -File)
    if ($distFiles.Count -lt 2) {
        throw "Production frontend does not contain the expected assets."
    }

    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
    $baseName = "english-practice-machine-$Version-windows-$shortCommit"
    $archivePath = Join-Path $outputDir "$baseName.zip"
    $checksumPath = "$archivePath.sha256"

    foreach ($path in @($archivePath, $checksumPath)) {
        if (-not (Test-Path -LiteralPath $path)) {
            continue
        }
        if (-not $Overwrite) {
            throw "Release output already exists: $path. Use -Overwrite to replace it."
        }
        Remove-Item -LiteralPath $path -Force
    }

    $archiveArgs = @(
        "archive",
        "--format=zip",
        "--output=$archivePath",
        "--prefix=$baseName/"
    )
    foreach ($file in $distFiles) {
        $relative = [System.IO.Path]::GetRelativePath(
            $projectRoot,
            $file.FullName
        ).Replace("\", "/")
        $archiveArgs += "--add-file=$relative"
    }
    $archiveArgs += "HEAD"

    Invoke-CheckedNative "Git release archive" { git @archiveArgs }
    if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
        throw "Git did not create the release archive."
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        $entries = @($archive.Entries | ForEach-Object { $_.FullName })
    }
    finally {
        $archive.Dispose()
    }

    $required = @(
        "$baseName/start.ps1",
        "$baseName/setup.ps1",
        "$baseName/run_app.py",
        "$baseName/requirements.txt",
        "$baseName/frontend/dist/index.html",
        "$baseName/docs/V1_WINDOWS_RELEASE.md",
        "$baseName/LICENSE"
    )
    foreach ($entry in $required) {
        if ($entry -notin $entries) {
            throw "Release archive is missing required entry: $entry"
        }
    }

    $forbidden = @(
        "(^|/)backend/data/",
        "(^|/)\.venv/",
        "(^|/)node_modules/",
        "(^|/)\.env($|\.)",
        "\.(db|sqlite|sqlite3)$"
    )
    foreach ($pattern in $forbidden) {
        $match = $entries | Where-Object { $_ -match $pattern } | Select-Object -First 1
        if ($match) {
            throw "Release archive contains forbidden private/generated entry: $match"
        }
    }

    $hash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
    Set-Content -LiteralPath $checksumPath -Encoding ascii -NoNewline -Value (
        "$hash  $([System.IO.Path]::GetFileName($archivePath))"
    )

    Write-Host "V1 Windows release package created."
    Write-Host "Commit: $commit"
    Write-Host "Archive: $archivePath"
    Write-Host "SHA256: $hash"
}
finally {
    Pop-Location
}
