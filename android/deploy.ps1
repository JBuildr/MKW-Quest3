[CmdletBinding()]
param(
    [string]$Adb = '',
    [string]$Package = 'org.mkwpc.quest',
    # The APK lives in the work directory, never in the repository (Fix AD); the pipeline
    # passes the path, and the default is the builder's default work directory.
    [string]$Apk = "$env:LOCALAPPDATA\mkw-quest\workspacendroid\out\mkw-quest.apk",
    # The DATA directory the installer already extracted from the user's own
    # disc image. Read from here, never written to, never copied into the repo.
    [string]$DataDirectory = "$env:APPDATA\CT-MKWII\Recomp\Install\GameAssets\DATA",
    # Retro Rewind is a Riivolution overlay, not a game: 209 tracks, characters
    # and UI that get layered over the disc above. It never replaces DataDirectory.
    # Empty: base game only (no overlay pushed, no retro_rewind_root written).
    [string]$RetroRewindDirectory = '',
    # Push the ~4.6 GB of game data. Off by default: it takes many minutes and
    # only has to happen once, while the APK is reinstalled on every build.
    [switch]$PushAssets,
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0

if ([string]::IsNullOrWhiteSpace($Adb)) {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'),
        (Join-Path $env:USERPROFILE 'Desktop\platform-tools\adb.exe')
    )
    $found = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $found) {
        $onPath = Get-Command adb -ErrorAction SilentlyContinue
        if ($onPath) { $found = $onPath.Source }
    }
    if (-not $found) { throw 'adb not found; pass -Adb <path to adb.exe>.' }
    $Adb = $found
}
foreach ($required in @($Adb, $Apk)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Not found: $required" }
}

$devices = & $Adb devices | Select-Object -Skip 1 | Where-Object { $_ -match '\sdevice$' }
if (-not $devices) { throw 'No device. Connect the Quest over USB and allow USB debugging on the headset.' }
Write-Host "Device: $devices"

if (-not $SkipInstall) {
    # -r keeps the app's data across reinstalls, -g pre-grants runtime permissions.
    & $Adb install -r -g $Apk
    if ($LASTEXITCODE -ne 0) { throw 'adb install failed' }
}

# The runtime prefers /sdcard/MKW when it exists (main.cpp, kAndroidPublicDataHome):
# reachable with adb push/pull without run-as, and outside the package directory
# so the game data survives an uninstall. Reading it needs MANAGE_EXTERNAL_STORAGE,
# which a sideloaded app can only get through appops; the grant is lost on every
# reinstall, hence it is repeated here every time.
$remote = '/sdcard/MKW'
& $Adb shell "mkdir -p $remote/WiiCompiled" | Out-Null
& $Adb shell "appops set $Package MANAGE_EXTERNAL_STORAGE allow"
if ($LASTEXITCODE -ne 0) { throw 'appops set failed' }

$hasRetro = -not [string]::IsNullOrWhiteSpace($RetroRewindDirectory)
if ($PushAssets) {
    $sources = @($DataDirectory)
    if ($hasRetro) { $sources += $RetroRewindDirectory }
    foreach ($source in $sources) {
        if (-not (Test-Path -LiteralPath $source)) { throw "Not found: $source" }
        # --sync: only files that are newer than the copy on the device are transferred, so a
        # Retro Rewind update pushes a few files instead of the whole 2 GB.
        Write-Host "Pushing $(Split-Path -Leaf $source) (only changed files; the first time takes a while)..."
        & $Adb push --sync "$source" "$remote/"
        if ($LASTEXITCODE -ne 0) { throw "adb push failed for $source" }
    }
}

# Written only when absent, so a Config.toml the user edited (window size,
# [debug] switches) survives redeploys.
$configRemote = "$remote/WiiCompiled/Config.toml"
$exists = & $Adb shell "test -f $configRemote && echo yes"
if ("$exists".Trim() -ne 'yes') {
    $retroLine = if ($hasRetro) { "retro_rewind_root = `"$remote/$(Split-Path -Leaf $RetroRewindDirectory)`"" } else { '' }
    $config = @"
# WiiCompiled user configuration -- Quest

[paths]
dvd_root = "$remote/DATA"
$retroLine

[video]
window_width = 1280
window_height = 720
"@
    $configPath = Join-Path $env:TEMP 'Config.toml'
    [IO.File]::WriteAllText($configPath, $config.Replace("`r`n", "`n"))
    & $Adb push $configPath $configRemote
    if ($LASTEXITCODE -ne 0) { throw 'pushing Config.toml failed' }
    Remove-Item -LiteralPath $configPath -Force
}

& $Adb shell "ls -la $remote $remote/WiiCompiled"

Write-Host ''
Write-Host 'Start the app from the headset (unknown sources); am start pauses the panel immediately.'
Write-Host 'Watch it with:'
Write-Host '  adb logcat -s mkw'
