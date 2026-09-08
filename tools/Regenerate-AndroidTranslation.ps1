[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$Workspace,
    # A clone of patchzyy/Wiicompiled at e6f9b21 with patches/0002 applied and
    # `dotnet build translator/Translator.sln -c Release` already run.
    [Parameter(Mandatory)] [string]$TranslatorSource,
    [string]$RetroRewindDirectory = "$env:APPDATA\CT-MKWII\RetroRewind\RetroRewind6",
    # The installed WiiCompiled PC build. After a Retro Rewind update the PC build has
    # already retranslated for the new Code.pul; when its base translation is the same
    # as ours (it usually is: the base only records which functions the mod patches),
    # its two metadata files are taken over so translate-mod accepts the new Code.pul.
    [string]$BuildWorkspace = '',
    [int]$Threads = [math]::Max(1, [math]::Min([Environment]::ProcessorCount, 16))
)

# The installer's translation is reused except for the two generators that
# write assembler files (.S): they emit PE/COFF section syntax because the
# shipped translator picks the syntax from the host it runs on. Patch 0002 adds
# --target-os; this script re-runs translate-mod, generate-data-init and
# emit-build-shards with it, mirroring the argument shapes of the workspace's
# own LocalBuild.ps1. The base translation (translate-recursive) emits no
# assembly and is kept as is.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0

$Workspace = [IO.Path]::GetFullPath($Workspace)
$translator = Join-Path $TranslatorSource 'translator\src\Translator.Cli\bin\Release\net8.0\Translator.Cli.exe'
if (-not (Test-Path -LiteralPath $translator)) {
    throw "Patched translator not built: $translator (run: dotnet build <clone>\translator\Translator.sln -c Release)"
}

$project = Join-Path $Workspace 'projects\mkwii\recomp.yml'
$generated = Join-Path $Workspace 'generated'
$functions = Join-Path $generated 'functions'
$baseMetadata = Join-Path $generated 'base_translation_output.json'
$baseManifest = Join-Path $Workspace 'build\base\mkwii_base_manifest.json'
$shards = Join-Path $generated 'build_shards'
$retroOut = Join-Path $Workspace 'build\mods\retro_rewind_full_cpp'
$codePul = Join-Path $RetroRewindDirectory 'Binaries\Code.pul'
$offlinePayload = Join-Path $Workspace 'Assets\OfflinePayload\binary\payload.RMCPD00.bin'

foreach ($required in @($project, $baseMetadata, $codePul)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Missing: $required" }
}

# Retro Rewind update: the base metadata must carry the current Code.pul's hash, else
# translate-mod refuses ("The base translation cannot be reused for this Code.pul").
$pulSha = (Get-FileHash -LiteralPath $codePul -Algorithm SHA256).Hash.ToLowerInvariant()
if (-not ([IO.File]::ReadAllText($baseMetadata)).Contains('"codePulSha256":"' + $pulSha + '"')) {
    $awareness = Join-Path $generated 'base_translation_mod_awareness.json'
    $pcMetadata = if ($BuildWorkspace) { Join-Path $BuildWorkspace 'generated\base_translation_output.json' } else { '' }
    $pcAwareness = if ($BuildWorkspace) { Join-Path $BuildWorkspace 'generated\base_translation_mod_awareness.json' } else { '' }
    $pcFunctions = if ($BuildWorkspace) { Join-Path $BuildWorkspace 'generated\functions' } else { '' }
    if ($pcMetadata -and (Test-Path -LiteralPath $pcMetadata) -and (Test-Path -LiteralPath $pcAwareness) -and
        ([IO.File]::ReadAllText($pcMetadata)).Contains('"codePulSha256":"' + $pulSha + '"')) {
        # Same base functions on both sides? Compare the function directories by name and size.
        $same = $true
        if (Test-Path -LiteralPath $pcFunctions) {
            $ours = Get-ChildItem -LiteralPath $functions -Recurse -File | ForEach-Object { $_.FullName.Substring($functions.Length) + ':' + $_.Length } | Sort-Object
            $theirs = Get-ChildItem -LiteralPath $pcFunctions -Recurse -File | ForEach-Object { $_.FullName.Substring($pcFunctions.Length) + ':' + $_.Length } | Sort-Object
            $same = ($ours.Count -eq $theirs.Count) -and (-not (Compare-Object $ours $theirs))
        }
        if ($same) {
            Write-Host 'Retro Rewind update: taking the base translation metadata of the PC build (same base functions).'
            $stamp = Get-Date -Format 'yyyy-MM-dd-HHmmss'
            Copy-Item -LiteralPath $baseMetadata -Destination "$baseMetadata.bak-$stamp" -Force
            if (Test-Path -LiteralPath $awareness) { Copy-Item -LiteralPath $awareness -Destination "$awareness.bak-$stamp" -Force }
            Copy-Item -LiteralPath $pcMetadata -Destination $baseMetadata -Force
            Copy-Item -LiteralPath $pcAwareness -Destination $awareness -Force
        } else {
            throw "The PC build's base translation differs from this work copy; run the PC build once (LocalBuild.ps1) and create the work copy again (New-QuestWorkspace.ps1)."
        }
    } else {
        throw "The base translation was made for another Code.pul and the PC build has not been retranslated for the installed one yet. Run the WiiCompiled PC build once (it retranslates the base), then try again with -BuildWorkspace."
    }
}
if (-not (Test-Path -LiteralPath $functions)) {
    # The installer may keep the base functions only inside the source bundle;
    # emit-build-shards needs the directory, so fail with a clear message.
    throw "Base functions directory not found: $functions. Run the PC build once (LocalBuild.ps1) so the base translation exists."
}
if (-not (Test-Path -LiteralPath $baseManifest)) {
    Write-Host 'Base manifest missing; creating it from the existing base translation...'
    [IO.Directory]::CreateDirectory((Split-Path -Parent $baseManifest)) | Out-Null
    & $translator emit-base-manifest --project $project --out (Split-Path -Parent $baseManifest) `
        --functions-dir $functions --translation-output-metadata $baseMetadata --region P
    if ($LASTEXITCODE -ne 0) { throw 'emit-base-manifest failed' }
}

$modArgs = @(
    'translate-mod', '--target-os', 'android', '--project', $project, '--profile', 'retro-rewind',
    '--base-manifest', $baseManifest, '--base-translation-output-metadata', $baseMetadata,
    '--code-pul', $codePul, '--mod-root', $RetroRewindDirectory, '--mod-name', 'Retro Rewind',
    '--region', 'P', '--out', $retroOut, '--prefer-cached-inputs', '--emit-cpp', '--threads', $Threads
)
if (Test-Path -LiteralPath $offlinePayload) {
    $modArgs += @('--retro-wfc-payload', $offlinePayload)
} else {
    $modArgs += '--skip-retro-wfc'
}
Write-Host 'translate-mod (Retro Rewind, target android)...'
& $translator @modArgs
if ($LASTEXITCODE -ne 0) { throw 'translate-mod failed' }

Write-Host 'generate-data-init (target android)...'
& $translator generate-data-init --target-os android --project $project
if ($LASTEXITCODE -ne 0) { throw 'generate-data-init failed' }

Write-Host 'emit-build-shards...'
& $translator emit-build-shards --target-os android --project $project --base-metadata $baseMetadata `
    --base-functions-dir $functions --native-source-dir (Join-Path $Workspace 'runtime\src') --out $shards `
    --resolved-profile (Join-Path $retroOut 'resolved_dispatch_profile.json') `
    --retro-cpp-dir (Join-Path $retroOut 'cpp')
if ($LASTEXITCODE -ne 0) { throw 'emit-build-shards failed' }

# The one line that must have changed: ELF section syntax instead of PE/COFF.
foreach ($asm in @((Join-Path $generated 'data_sections_init_blobs.S'), (Join-Path $retroOut 'cpp\mod_data_patches_blobs.S'))) {
    if ((Test-Path -LiteralPath $asm) -and (Select-String -LiteralPath $asm -Pattern '\.rdata' -Quiet)) {
        throw "$asm still carries Windows section syntax; the translator was not built with patch 0002."
    }
}
Write-Host 'Translated output is ready for Android. Next: tools\Build-Quest.ps1'
