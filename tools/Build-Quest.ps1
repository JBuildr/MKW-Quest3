[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$Workspace,
    [string]$Sdk = "$env:LOCALAPPDATA\Android\Sdk",
    # Any NDK 28.x works; the exact directory name is what changes.
    [string]$NdkVersion = '',
    [string]$BuildToolsVersion = '35.0.0',
    [string]$Platform = 'android-34',
    # CMake and Ninja: the WiiCompiled toolkit ships both; the SDK's own CMake
    # or a system install works as well.
    [string]$Toolkit = "$env:APPDATA\CT-MKWII\Recomp\Install\Toolkit",
    # The final link needs close to 15 GB; more parallel compiles on a 16 GB
    # machine get the build killed by the system. Raise on bigger machines.
    [int]$Jobs = 4,
    # RetroRewind (needs the translated mod) or WiiCompiled (the base game).
    [ValidateSet('RetroRewind', 'WiiCompiled')] [string]$Product = 'RetroRewind',
    [switch]$SkipConfigure,
    [switch]$SkipApk
)

# Configures, builds and packages the Quest build of the work copy created by
# New-QuestWorkspace.ps1 and prepared by Regenerate-AndroidTranslation.ps1.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0

$Workspace = [IO.Path]::GetFullPath($Workspace)
$buildDir = Join-Path $Workspace 'build-android'
$repoRoot = Split-Path -Parent $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($NdkVersion)) {
    $ndkRoot = Join-Path $Sdk 'ndk'
    $candidates = @(Get-ChildItem -LiteralPath $ndkRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like '28.*' } | Sort-Object Name -Descending)
    if (-not $candidates) { throw "No NDK 28.x under $ndkRoot" }
    $NdkVersion = $candidates[0].Name
}
$toolchainFile = Join-Path $Sdk "ndk\$NdkVersion\build\cmake\android.toolchain.cmake"
if (-not (Test-Path -LiteralPath $toolchainFile)) { throw "NDK toolchain file not found: $toolchainFile" }

# CMake and Ninja: WiiCompiled toolkit, else the SDK's own copies (SDK Manager,
# "CMake" package), else whatever is on PATH.
$sdkCmakeRoots = @(Get-ChildItem -LiteralPath (Join-Path $Sdk 'cmake') -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending)
$cmake = @((Join-Path $Toolkit 'CMake\bin\cmake.exe')) + @($sdkCmakeRoots | ForEach-Object { Join-Path $_.FullName 'bin\cmake.exe' }) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
$ninja = @((Join-Path $Toolkit 'Ninja\ninja.exe')) + @($sdkCmakeRoots | ForEach-Object { Join-Path $_.FullName 'bin\ninja.exe' }) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $cmake) { $cmake = 'cmake' }
if (-not $ninja) { $ninja = 'ninja' }
foreach ($tool in @($cmake, $ninja)) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool not found. Install the SDK Manager's CMake package or the WiiCompiled toolkit." }
}

if (-not $SkipConfigure) {
    Write-Host "Configuring for arm64-v8a / $Platform with NDK $NdkVersion..."
    & $cmake -S (Join-Path $Workspace 'runtime') -B $buildDir -G Ninja `
        "-DCMAKE_TOOLCHAIN_FILE=$toolchainFile" `
        '-DANDROID_ABI=arm64-v8a' "-DANDROID_PLATFORM=$Platform" `
        '-DCMAKE_BUILD_TYPE=Release' '-DMKW_BUILD_PRODUCTS=ON' `
        '-DAURORA_DAWN_PROVIDER=package' "-DCMAKE_MAKE_PROGRAM=$ninja"
    if ($LASTEXITCODE -ne 0) { throw 'CMake configure failed' }
}

Write-Host "Building lib$Product.so with $Jobs parallel jobs (this takes long)..."
& $ninja -C $buildDir -j $Jobs $Product
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
$library = Join-Path $buildDir "lib$Product.so"
if (-not (Test-Path -LiteralPath $library)) { throw "Build finished but $library is missing" }

if ($SkipApk) { return }

# build-apk.ps1 resolves its resources through $PSScriptRoot, which is empty
# when the script is started with powershell -File from some shells; invoking
# it as a script block keeps it set. The output directory is passed explicitly
# for the same reason, and it lies in the work directory: the APK carries the
# translated game code and must not land inside this repository (Fix AD).
$packaging = Join-Path $repoRoot 'android\build-apk.ps1'
$apkDir = Join-Path $Workspace 'android\out'
& $packaging -Sdk $Sdk -BuildToolsVersion $BuildToolsVersion -Platform $Platform `
    -NativeLibrary $library `
    -ExtraLibraries (Join-Path $buildDir '_deps\png-build\libpng16.so') `
    -SdlSourceDir (Join-Path $buildDir '_deps\sdl-src') `
    -OutputDirectory $apkDir
Write-Host "APK: $(Join-Path $apkDir 'mkw-quest.apk')"
Write-Host "Next: android\deploy.ps1 -Apk `"$(Join-Path $apkDir 'mkw-quest.apk')`" -PushAssets (first time), without -PushAssets afterwards"
