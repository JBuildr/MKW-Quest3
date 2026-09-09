[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$Workspace,
    # Standalone mode: the user's own disc dump (ISO/WBFS/RVZ/...). Everything is
    # built from it; no WiiCompiled PC installation is needed.
    [string]$DiscImage = '',
    # Optional Retro Rewind 6 directory (Get-RetroRewind.ps1 or WheelWizard). Empty: base game only.
    [string]$RetroRewindDirectory = '',
    # Before translating, install or update Retro Rewind in -RetroRewindDirectory from its
    # update server (tools\Get-RetroRewind.ps1). Needs internet.
    [switch]$UpdateRetroRewind,
    # Legacy mode (used when -DiscImage is empty): reuse the translation of an
    # installed WiiCompiled PC build.
    [string]$BuildWorkspace = "$env:APPDATA\CT-MKWII\Recomp\Install\BuildWorkspace",
    [string]$Toolkit = "$env:APPDATA\CT-MKWII\Recomp\Install\Toolkit",
    [string]$Sdk = "$env:LOCALAPPDATA\Android\Sdk",
    # Parallel compiles for the native build; 0 = from free memory (Build-Quest.ps1).
    [int]$Jobs = 0,
    [switch]$Install,
    [switch]$PushAssets,
    # Skip the build and only install what <Workspace>\android\out already holds.
    [switch]$InstallOnly
)

# One entry point for the whole pipeline, used by the GUI and from a terminal.
# Every step is skipped when its result exists, so a run after a failure
# continues where it stopped.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0

$repoRoot = Split-Path -Parent $PSScriptRoot
$tools = $PSScriptRoot
$Workspace = [IO.Path]::GetFullPath($Workspace)
$standalone = -not [string]::IsNullOrWhiteSpace($DiscImage)
$buildsRetro = -not [string]::IsNullOrWhiteSpace($RetroRewindDirectory)
$product = if ($buildsRetro -or -not $standalone) { 'RetroRewind' } else { 'WiiCompiled' }
$pinnedCommit = 'e6f9b21'

function Step([string]$Title) { Write-Host ''; Write-Host "==> $Title" }

# True when the translation on disk was made from the Retro Rewind Code.pul that is installed
# now. Translate-Game.ps1 stages a copy of the Code.pul it translates; a Retro Rewind update
# (WheelWizard) replaces the original, and the Retro WFC server refuses builds of the old pack.
function Test-TranslatedCodePul([string]$stagedCodePul) {
    if (-not $buildsRetro) { return $true }
    $current = Join-Path $RetroRewindDirectory 'Binaries\Code.pul'
    if (-not (Test-Path -LiteralPath $stagedCodePul) -or -not (Test-Path -LiteralPath $current)) { return $false }
    return (Get-FileHash -LiteralPath $stagedCodePul -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $current -Algorithm SHA256).Hash
}

if (-not $InstallOnly) {
    if ($UpdateRetroRewind) {
        if (-not $buildsRetro) { throw '-UpdateRetroRewind needs -RetroRewindDirectory.' }
        Step 'Retro Rewind (install or update from the update server)'
        # Runs in-process: its throw aborts the pipeline (no native exit code to read here).
        & (Join-Path $tools 'Get-RetroRewind.ps1') -RetroRewindDirectory $RetroRewindDirectory
    }
    if ($standalone) {
        Step 'Work copy (clone of WiiCompiled at the pinned commit, patched)'
        & (Join-Path $tools 'New-StandaloneWorkspace.ps1') -Workspace $Workspace -Commit $pinnedCommit

        Step 'Disc image'
        $assetsDol = Join-Path $Workspace 'Assets\main.dol'
        if ((Test-Path -LiteralPath $assetsDol) -and (Test-Path -LiteralPath (Join-Path $Workspace 'GameAssets\DATA\sys\main.dol'))) {
            Write-Host 'Already extracted and validated.'
        } else {
            & (Join-Path $tools 'Import-DiscImage.ps1') -Workspace $Workspace -DiscImage $DiscImage
        }

        Step 'Translation for Android'
        $shards = Join-Path $Workspace 'generated\build_shards\shards.cmake'
        $retroCpp = Join-Path $Workspace 'build\mods\retro_rewind_full_cpp\cpp'
        $stagedCodePul = Join-Path $Workspace 'PulsarPacks\completed\RetroRewind\RetroRewind6\Binaries\Code.pul'
        $done = (Test-Path -LiteralPath $shards) -and (-not $buildsRetro -or (Test-Path -LiteralPath $retroCpp))
        if ($done -and -not (Test-TranslatedCodePul $stagedCodePul)) {
            Write-Host 'Retro Rewind was updated since the last translation; translating the mod again.'
            $done = $false
        }
        if ($done) {
            Write-Host 'Already translated.'
        } else {
            & (Join-Path $tools 'Translate-Game.ps1') -Workspace $Workspace -RetroRewindDirectory $RetroRewindDirectory
        }
    } else {
        Step 'Checking the WiiCompiled installation'
        # Legacy mode lives in the source tree only; the release archive carries just the
        # disc-image path.
        foreach ($legacyScript in @('New-QuestWorkspace.ps1', 'Regenerate-AndroidTranslation.ps1')) {
            if (-not (Test-Path -LiteralPath (Join-Path $tools $legacyScript))) {
                throw "No disc image given, and the legacy mode ($legacyScript) is not part of the release archive. Pick your disc image, or clone the full repository."
            }
        }
        $shippedTranslator = Join-Path $Toolkit 'Translator\Translator.Cli.exe'
        if (Test-Path -LiteralPath $shippedTranslator) {
            $version = (Get-Item -LiteralPath $shippedTranslator).VersionInfo.ProductVersion
            Write-Host "Installed translator: $version"
            if ($version -notmatch $pinnedCommit) {
                Write-Warning "The installed WiiCompiled is not the version this port was made against ($pinnedCommit). The patches are verified before they are applied; if they fail, this is why."
            }
        }
        if (-not $buildsRetro) { $RetroRewindDirectory = "$env:APPDATA\CT-MKWII\RetroRewind\RetroRewind6" }

        Step 'Work copy'
        if (Test-Path -LiteralPath (Join-Path $Workspace 'runtime\CMakeLists.txt')) {
            Write-Host "Exists: $Workspace"
        } else {
            & (Join-Path $tools 'New-QuestWorkspace.ps1') -Workspace $Workspace -BuildWorkspace $BuildWorkspace
        }

        Step 'Patched translator'
        $translatorSource = Join-Path (Split-Path -Parent $Workspace) 'wiicompiled-src'
        $translatorExe = Join-Path $translatorSource 'translator\src\Translator.Cli\bin\Release\net8.0\Translator.Cli.exe'
        if (-not (Test-Path -LiteralPath $translatorExe)) {
            & (Join-Path $tools 'New-StandaloneWorkspace.ps1') -Workspace $translatorSource -Commit $pinnedCommit
        }

        Step 'Translated output for Android'
        $blobs = Join-Path $Workspace 'generated\data_sections_init_blobs.S'
        $retroCpp = Join-Path $Workspace 'build\mods\retro_rewind_full_cpp\cpp'
        $codePul = Join-Path $RetroRewindDirectory 'Binaries\Code.pul'
        $regenerated = (Test-Path -LiteralPath $blobs) -and -not (Select-String -LiteralPath $blobs -Pattern '\.rdata' -Quiet)
        if ($regenerated -and (Test-Path -LiteralPath $retroCpp) -and (Test-Path -LiteralPath $codePul) -and
            ((Get-Item -LiteralPath $codePul).LastWriteTimeUtc -gt (Get-Item -LiteralPath $retroCpp).LastWriteTimeUtc)) {
            Write-Host 'Retro Rewind was updated since the last translation; translating the mod again.'
            $regenerated = $false
        }
        if ($regenerated) {
            Write-Host 'Already regenerated (ELF section syntax present).'
        } else {
            & (Join-Path $tools 'Regenerate-AndroidTranslation.ps1') -Workspace $Workspace `
                -TranslatorSource $translatorSource -RetroRewindDirectory $RetroRewindDirectory -BuildWorkspace $BuildWorkspace
        }
    }

    Step "Build and package ($product)"
    & (Join-Path $tools 'Build-Quest.ps1') -Workspace $Workspace -Sdk $Sdk -Toolkit $Toolkit -Jobs $Jobs -Product $product
}

if ($Install -or $InstallOnly) {
    Step 'Install on the Quest'
    $deploy = Join-Path $repoRoot 'android\deploy.ps1'
    # The APK is written to the work directory (Build-Quest.ps1), never into the repository.
    $deployArgs = @{ Apk = (Join-Path $Workspace 'android\out\mkw-quest.apk') }
    if ($standalone) { $deployArgs.DataDirectory = Join-Path $Workspace 'GameAssets\DATA' }
    if ($buildsRetro -or -not $standalone) { $deployArgs.RetroRewindDirectory = $RetroRewindDirectory }
    if ($PushAssets) { $deployArgs.PushAssets = $true }
    & $deploy @deployArgs
}

Write-Host ''
Write-Host 'Done.'
