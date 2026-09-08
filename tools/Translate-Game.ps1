[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$Workspace,
    # Retro Rewind 6 directory (the WheelWizard download). Empty: base game only.
    [string]$RetroRewindDirectory = '',
    # Build without the Retro WFC online payload. By default the translator fetches the
    # current payload from the Retro WFC server (URL in projects\mkwii\recomp.yml), which
    # online play needs; the server rejects builds whose pack version is stale.
    [switch]$SkipRetroWfc,
    [int]$Threads = [math]::Max(1, [math]::Min([Environment]::ProcessorCount, 16))
)

# Full translation for Android in a standalone work copy: the same steps as
# WiiCompiled's LocalBuild.ps1 (same arguments), plus --target-os android on
# every call so the generated assembly is ELF. Needs Assets\main.dol and
# Assets\StaticR.rel from Import-DiscImage.ps1 and the translator built by
# New-StandaloneWorkspace.ps1.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0

$Workspace = [IO.Path]::GetFullPath($Workspace)
$translator = Join-Path $Workspace 'translator\src\Translator.Cli\bin\Release\net8.0\Translator.Cli.exe'
$project = Join-Path $Workspace 'projects\mkwii\recomp.yml'
$generated = Join-Path $Workspace 'generated'
$functions = Join-Path $generated 'functions'
$baseMetadata = Join-Path $generated 'base_translation_output.json'
$baseManifestDir = Join-Path $Workspace 'build\base'
$baseManifest = Join-Path $baseManifestDir 'mkwii_base_manifest.json'
$shards = Join-Path $generated 'build_shards'
$retroOut = Join-Path $Workspace 'build\mods\retro_rewind_full_cpp'
$buildsRetro = -not [string]::IsNullOrWhiteSpace($RetroRewindDirectory)

foreach ($required in @($translator, $project, (Join-Path $Workspace 'Assets\main.dol'), (Join-Path $Workspace 'Assets\StaticR.rel'))) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Missing: $required" }
}
$entryPoint = ''
$section = ''
foreach ($raw in [IO.File]::ReadAllLines($project)) {
    $line = ($raw -replace '#.*$', '')
    if ($line -match '^([A-Za-z0-9_]+):') { $section = $Matches[1]; continue }
    if ($section -eq 'translation' -and $entryPoint -eq '' -and $line -match '^\s*-\s*(0[xX][0-9a-fA-F]+)\s*$') { $entryPoint = $Matches[1] }
}
if (-not $entryPoint) { throw "No translation entry point in $project" }

if ($buildsRetro) {
    $codePul = Join-Path $RetroRewindDirectory 'Binaries\Code.pul'
    if (-not (Test-Path -LiteralPath $codePul)) { throw "Retro Rewind Code.pul not found: $codePul" }
    # The base translation must know the mod's patch set (leaf inlining), so the
    # project's mod_root gets the selected Code.pul before the base leg runs.
    $staged = Join-Path $Workspace 'PulsarPacks\completed\RetroRewind\RetroRewind6\Binaries'
    [IO.Directory]::CreateDirectory($staged) | Out-Null
    Copy-Item -LiteralPath $codePul -Destination (Join-Path $staged 'Code.pul') -Force
}

[IO.Directory]::CreateDirectory($generated) | Out-Null
[IO.Directory]::CreateDirectory($baseManifestDir) | Out-Null

# The base translation (the long step) is reused when it exists and, for a Retro Rewind
# build, when the translator confirms it was made around the same patch set as the current
# Code.pul (check-base-mod-awareness, exit 0 = reuse). This is what makes a Retro Rewind
# update cheap: only the mod leg and its shards are redone.
$reuseBase = (Test-Path -LiteralPath $baseMetadata) -and (Test-Path -LiteralPath $functions) -and (Test-Path -LiteralPath $baseManifest)
if ($reuseBase -and $buildsRetro) {
    & $translator check-base-mod-awareness --project $project --profile retro-rewind `
        --translation-output-metadata $baseMetadata --code-pul $codePul | Write-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'The base translation is stale for this Code.pul; retranslating the base game.'
        $reuseBase = $false
    }
}
if ($reuseBase) {
    Write-Host 'Reusing the completed base translation.'
} else {
    Write-Host "translate-recursive from $entryPoint (this is the long step)..."
    & $translator translate-recursive $entryPoint --target-os android --project $project `
        --outdir $functions --output-metadata $baseMetadata `
        --production-source-bundle (Join-Path $generated 'base_translation_sources.bin') `
        --no-function-files --prune-stale --threads $Threads
    if ($LASTEXITCODE -ne 0) { throw 'translate-recursive failed' }

    Write-Host 'emit-base-manifest...'
    & $translator emit-base-manifest --target-os android --project $project --out $baseManifestDir `
        --functions-dir $functions --translation-output-metadata $baseMetadata --region P
    if ($LASTEXITCODE -ne 0) { throw 'emit-base-manifest failed' }
}

if ($buildsRetro) {
    Write-Host 'translate-mod (Retro Rewind)...'
    $modArgs = @('translate-mod', '--target-os', 'android', '--project', $project, '--profile', 'retro-rewind',
        '--base-manifest', $baseManifest, '--base-translation-output-metadata', $baseMetadata,
        '--code-pul', $codePul, '--mod-root', $RetroRewindDirectory, '--mod-name', 'Retro Rewind',
        '--region', 'P', '--out', $retroOut, '--prefer-cached-inputs', '--emit-cpp', '--threads', $Threads)
    if ($SkipRetroWfc) {
        Write-Host '  (without the Retro WFC payload: no online play)'
        $modArgs += '--skip-retro-wfc'
    } else {
        Write-Host '  (the Retro WFC payload is fetched from the Retro WFC server; needs internet)'
    }
    & $translator @modArgs
    if ($LASTEXITCODE -ne 0) { throw 'translate-mod failed' }
}

Write-Host 'generate-data-init...'
& $translator generate-data-init --target-os android --project $project
if ($LASTEXITCODE -ne 0) { throw 'generate-data-init failed' }

Write-Host 'emit-build-shards...'
$shardArgs = @('emit-build-shards', '--target-os', 'android', '--project', $project, '--base-metadata', $baseMetadata,
    '--base-functions-dir', $functions, '--native-source-dir', (Join-Path $Workspace 'runtime\src'), '--out', $shards)
if ($buildsRetro) {
    $shardArgs += @('--resolved-profile', (Join-Path $retroOut 'resolved_dispatch_profile.json'), '--retro-cpp-dir', (Join-Path $retroOut 'cpp'))
}
& $translator @shardArgs
if ($LASTEXITCODE -ne 0) { throw 'emit-build-shards failed' }

foreach ($asm in @((Join-Path $generated 'data_sections_init_blobs.S'), (Join-Path $retroOut 'cpp\mod_data_patches_blobs.S'))) {
    if ((Test-Path -LiteralPath $asm) -and (Select-String -LiteralPath $asm -Pattern '\.rdata' -Quiet)) {
        throw "$asm carries Windows section syntax; the translator in the work copy lacks patch 0002."
    }
}
Write-Host 'Translation for Android complete.'
