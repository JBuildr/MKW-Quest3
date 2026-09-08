[CmdletBinding()]
param(
    # Version label that becomes part of the file name, e.g. v0.1.0.
    [Parameter(Mandatory)] [string]$Version,
    # Where the zip is written. Default: next to the repository, never inside it.
    [string]$OutputDirectory = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
)

# Packs what a user needs to build the APK from a disc image: the builder, the pipeline
# scripts, the patches and the Android packaging files, plus README, license and notices.
# Nothing else: no development harness, no legacy-mode scripts (those need a WiiCompiled PC
# installation and stay in the source tree), no git metadata, and of course no game data
# (the repository never holds any). Upload the result as the release asset; the source archive GitHub adds on its
# own covers the rest.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0

$repoRoot = Split-Path -Parent $PSScriptRoot
$name = "MKW-Quest3-Port-$Version"
$staging = Join-Path ([IO.Path]::GetTempPath()) $name
$zip = Join-Path $OutputDirectory "$name.zip"

$include = @(
    'Quest-Builder.cmd', 'README.md', 'LICENSE', 'THIRD-PARTY-NOTICES.md',
    'tools\Build-Quest.ps1', 'tools\Get-RetroRewind.ps1', 'tools\Import-DiscImage.ps1',
    'tools\Invoke-QuestPipeline.ps1', 'tools\New-StandaloneWorkspace.ps1',
    'tools\Quest-Builder.ps1', 'tools\Translate-Game.ps1', 'tools\README.md',
    'patches\0001-android-buildworkspace.patch', 'patches\0002-translator-target-os.patch',
    'patches\0003-quest-adreno-workaround.patch', 'patches\README.md',
    'android\AndroidManifest.xml', 'android\build-apk.ps1', 'android\deploy.ps1',
    'android\java\org\mkwpc\quest\MkwActivity.java', 'android\res\values\strings.xml'
)

if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
foreach ($relative in $include) {
    $source = Join-Path $repoRoot $relative
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing: $source" }
    $target = Join-Path $staging $relative
    [IO.Directory]::CreateDirectory((Split-Path -Parent $target)) | Out-Null
    Copy-Item -LiteralPath $source -Destination $target
}

# Last check before packing: nothing that points at one machine may ship.
$hits = Get-ChildItem -LiteralPath $staging -Recurse -File |
    Select-String -Pattern 'C:\\Users\\|/Users/|AppData\\Local\\Temp' -SimpleMatch:$false |
    Where-Object { $_.Line -notmatch '\$env:' }
if ($hits) { $hits | ForEach-Object { Write-Warning "$($_.Path):$($_.LineNumber): $($_.Line.Trim())" }; throw 'Machine-specific paths found; fix them before packing.' }

if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
[IO.Directory]::CreateDirectory($OutputDirectory) | Out-Null
Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $zip -CompressionLevel Optimal
Remove-Item -LiteralPath $staging -Recurse -Force
Write-Host "Release archive: $zip ($([math]::Round((Get-Item -LiteralPath $zip).Length / 1KB)) KB, $($include.Count) files)"
