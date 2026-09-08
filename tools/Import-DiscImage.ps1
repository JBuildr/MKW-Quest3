[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$Workspace,
    # The user's own dump: ISO, WBFS, RVZ, WIA, GCZ, CISO or NKit; nodtool reads all of them.
    [Parameter(Mandatory)] [string]$DiscImage,
    # nodtool.exe (encounter/nod, MIT/Apache-2.0). Taken from the WiiCompiled toolkit
    # when installed, otherwise downloaded once into the work copy.
    [string]$NodTool = ''
)

# Validates the disc (game ID and the sha256 of main.dol / StaticR.rel pinned in
# projects/mkwii/recomp.yml) and extracts it to <workspace>\GameAssets\DATA, which
# is also what gets pushed to the headset as dvd_root. Nothing is copied anywhere
# near this repository.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0

$Workspace = [IO.Path]::GetFullPath($Workspace)
$DiscImage = [IO.Path]::GetFullPath($DiscImage)
if (-not (Test-Path -LiteralPath $DiscImage -PathType Leaf)) { throw "Disc image not found: $DiscImage" }
$project = Join-Path $Workspace 'projects\mkwii\recomp.yml'
if (-not (Test-Path -LiteralPath $project)) { throw "Not a work copy (no projects\mkwii\recomp.yml): $Workspace" }

# Pins from recomp.yml, parsed the way WiiCompiled's own LocalBuild.ps1 does.
$pins = @{ GameId = ''; DolSha256 = ''; RelSha256 = '' }
$section = ''; $inputKey = ''
foreach ($raw in [IO.File]::ReadAllLines($project)) {
    $line = ($raw -replace '#.*$', '')
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    if ($line -match '^([A-Za-z0-9_]+):') { $section = $Matches[1]; $inputKey = ''; continue }
    if ($section -eq 'inputs' -and $line -match '^\s{2}([A-Za-z0-9_]+):\s*$') { $inputKey = $Matches[1]; continue }
    if ($section -eq 'project' -and $line -match '^\s*game_id:\s*(\S+)\s*$') { $pins.GameId = $Matches[1] }
    elseif ($section -eq 'inputs' -and $line -match '^\s*sha256:\s*([0-9a-fA-F]{64})\s*$') {
        if ($inputKey -eq 'dol') { $pins.DolSha256 = $Matches[1].ToLowerInvariant() }
        elseif ($inputKey -eq 'rel') { $pins.RelSha256 = $Matches[1].ToLowerInvariant() }
    }
}
if (-not $pins.GameId -or -not $pins.DolSha256 -or -not $pins.RelSha256) { throw "Could not read the pins from $project" }

if ([string]::IsNullOrWhiteSpace($NodTool)) {
    $candidates = @("$env:APPDATA\CT-MKWII\Recomp\Install\Toolkit\nodtool.exe", (Join-Path $Workspace 'tools-cache\nodtool.exe'))
    $NodTool = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $NodTool) {
        $NodTool = Join-Path $Workspace 'tools-cache\nodtool.exe'
        [IO.Directory]::CreateDirectory((Split-Path -Parent $NodTool)) | Out-Null
        $arch = if ([Environment]::Is64BitOperatingSystem) { 'x86_64' } else { 'x86' }
        $url = "https://github.com/encounter/nod/releases/download/v2.0.0-alpha.10/nodtool-windows-$arch.exe"
        Write-Host "Downloading nodtool from $url"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $NodTool
    }
}

Write-Host 'Reading the disc header...'
# Same PowerShell 5.1 rule as in New-StandaloneWorkspace.ps1: a redirected stderr line would
# abort the script under $ErrorActionPreference = 'Stop', so the call runs with Continue.
$ErrorActionPreference = 'Continue'
$info = & $NodTool info $DiscImage 2>&1 | Out-String
$ErrorActionPreference = 'Stop'
if ($LASTEXITCODE -ne 0) { throw "nodtool could not read this disc image: $info" }
if ($info -notmatch '(?m)^Game ID: (\S+)') { throw "nodtool did not report a game ID:`n$info" }
$gameId = $Matches[1]
if ($gameId -ne $pins.GameId) {
    throw "This disc is '$gameId', not '$($pins.GameId)' (Mario Kart Wii, PAL). Only that exact game and region works."
}

$dataDir = Join-Path $Workspace 'GameAssets\DATA'
if (Test-Path -LiteralPath $dataDir) { Remove-Item -LiteralPath $dataDir -Recurse -Force }
Write-Host "Extracting the disc to $dataDir (a few minutes)..."
& $NodTool extract $DiscImage $dataDir -q
if ($LASTEXITCODE -ne 0) { throw 'nodtool extract failed' }

$dol = Join-Path $dataDir 'sys\main.dol'
$rel = Join-Path $dataDir 'files\rel\StaticR.rel'
foreach ($f in @($dol, $rel)) { if (-not (Test-Path -LiteralPath $f)) { throw "Extraction did not produce $f" } }
$dolSha = (Get-FileHash -LiteralPath $dol -Algorithm SHA256).Hash.ToLowerInvariant()
$relSha = (Get-FileHash -LiteralPath $rel -Algorithm SHA256).Hash.ToLowerInvariant()
if ($dolSha -ne $pins.DolSha256) { throw "main.dol does not match the pinned revision (got $dolSha). This dump is not the disc revision the translation project expects." }
if ($relSha -ne $pins.RelSha256) { throw "StaticR.rel does not match the pinned revision (got $relSha)." }

$assets = Join-Path $Workspace 'Assets'
[IO.Directory]::CreateDirectory($assets) | Out-Null
Copy-Item -LiteralPath $dol -Destination (Join-Path $assets 'main.dol') -Force
Copy-Item -LiteralPath $rel -Destination (Join-Path $assets 'StaticR.rel') -Force
Write-Host "Disc validated ($gameId) and extracted."
