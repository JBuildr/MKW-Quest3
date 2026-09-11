[CmdletBinding()]
param(
    # Defaults match this machine; both are also read from local.paths.md.
    [string]$Sdk = "$env:LOCALAPPDATA\Android\Sdk",
    [string]$BuildToolsVersion = '35.0.0',
    [string]$Platform = 'android-34',
    # Which shell the APK asks Horizon OS for: Panel is the flat 2D window, so
    # an APK built without this parameter is the same one as before OpenXR
    # existed; Immersive is the OpenXR app. There is no manifest merger here,
    # the two manifests are complete files and this picks one of them.
    [ValidateSet('Panel', 'Immersive')] [string]$Shell = 'Panel',
    [Parameter(Mandatory)] [string]$NativeLibrary,   # libmain.so for arm64-v8a
    # Anything libmain.so lists as NEEDED that Android does not already provide
    # (libpng16.so here; libz.so is a system library).
    [string[]]$ExtraLibraries = @(),
    [Parameter(Mandatory)] [string]$SdlSourceDir,    # the fetched SDL3 source tree
    [string]$OutputDirectory = "$PSScriptRoot\out"
)

# No Gradle: the SDK's own aapt2/d8/apksigner do the whole job, and a sideload
# only needs a debug key. Gradle would add a toolchain to install and keep in
# sync for no gain at this stage.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0

$tools = Join-Path $Sdk "build-tools\$BuildToolsVersion"
$androidJar = Join-Path $Sdk "platforms\$Platform\android.jar"
$manifestName = if ($Shell -eq 'Immersive') { 'AndroidManifest.vr.xml' } else { 'AndroidManifest.xml' }
$manifest = Join-Path $PSScriptRoot $manifestName
# The manifest is guarded like every other input: aapt2 reports a missing
# --manifest as a generic link failure, which reads like a resource problem.
foreach ($required in @($tools, $androidJar, $manifest, $NativeLibrary, $SdlSourceDir)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Not found: $required" }
}

$aapt2 = Join-Path $tools 'aapt2.exe'
$d8 = Join-Path $tools 'd8.bat'
$zipalign = Join-Path $tools 'zipalign.exe'
$apksigner = Join-Path $tools 'apksigner.bat'

$work = Join-Path $OutputDirectory 'work'
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $work, "$work\res", "$work\classes", "$work\dex" | Out-Null

# ---- resources ------------------------------------------------------------
& $aapt2 compile --dir "$PSScriptRoot\res" -o "$work\res.zip"
if ($LASTEXITCODE -ne 0) { throw 'aapt2 compile failed' }

$unsigned = Join-Path $work 'unsigned.apk'
& $aapt2 link -o $unsigned -I $androidJar `
    --manifest $manifest `
    --java "$work\gen" `
    "$work\res.zip"
if ($LASTEXITCODE -ne 0) { throw 'aapt2 link failed' }

# ---- java ------------------------------------------------------------------
# SDLActivity and friends come from the SDL source tree that CMake already
# fetched, so the Java side can never drift from the linked native SDL.
$sdlJava = Join-Path $SdlSourceDir 'android-project\app\src\main\java'
if (-not (Test-Path -LiteralPath $sdlJava)) { throw "SDL Java sources not found: $sdlJava" }

# ---- aurora surface hook --------------------------------------------------
# On Android aurora starts with its surface marked "not ready"
# (aurora-main/lib/window.cpp: g_surfaceReady = false) and the only thing that
# clears it is the JNI function Java_org_libsdl_app_SDLSurface_auroraNativeSetSurfaceReady.
# While it is false, aurora::window::is_paused() is true and poll_events()
# blocks in SDL_WaitEvent() -- called from inside the VI retrace, so the guest
# never sees a retrace and the panel stays black (a black panel otherwise).
# Stock SDL knows nothing of that hook and aurora ships no Java, so the two
# calls are spliced into SDL's SDLSurface.java here, at build time. Splicing
# instead of keeping a copy of the file keeps SDL's version the authority: if
# an SDL update moves an anchor, the build fails here instead of compiling a
# stale copy of the whole class.
$sdlSurface = Join-Path $sdlJava 'org\libsdl\app\SDLSurface.java'
$surfaceText = [IO.File]::ReadAllText($sdlSurface)
$nl = if ($surfaceText.Contains("`r`n")) { "`r`n" } else { "`n" }
$splices = @(
    @{  # the native declaration plus a guard that survives a missing symbol
        Anchor = '    protected Surface getNativeSurface() {'
        Before = "    public static native void auroraNativeSetSurfaceReady(boolean ready);$nl$nl" +
                 "    private static void auroraSetSurfaceReady(boolean ready) {$nl" +
                 "        try {$nl" +
                 "            auroraNativeSetSurfaceReady(ready);$nl" +
                 "        } catch (UnsatisfiedLinkError e) {$nl" +
                 "            Log.w(`"SDL`", `"auroraNativeSetSurfaceReady unavailable: `" + e);$nl" +
                 "        }$nl" +
                 "    }$nl$nl"
        After  = ''
    },
    @{  # surfaceChanged(): the surface is valid once SDL itself says so
        Anchor = '        mIsSurfaceReady = true;'
        Before = ''
        After  = "$nl        auroraSetSurfaceReady(true);"
    },
    @{  # surfaceDestroyed(): withdraw it before SDL tears the surface down
        Anchor = '        SDLActivity.onNativeSurfaceDestroyed();'
        Before = "        auroraSetSurfaceReady(false);$nl"
        After  = ''
    }
)
foreach ($splice in $splices) {
    $count = ([regex]::Matches($surfaceText, [regex]::Escape($splice.Anchor))).Count
    if ($count -ne 1) { throw "SDLSurface.java: anchor '$($splice.Anchor.Trim())' found $count times, expected 1" }
    $surfaceText = $surfaceText.Replace($splice.Anchor, $splice.Before + $splice.Anchor + $splice.After)
}
$patchedDir = Join-Path $work 'java\org\libsdl\app'
New-Item -ItemType Directory -Force -Path $patchedDir | Out-Null
[IO.File]::WriteAllText((Join-Path $patchedDir 'SDLSurface.java'), $surfaceText, (New-Object Text.UTF8Encoding $false))

$sources = @()
$sources += (Get-ChildItem -Recurse -Filter *.java -LiteralPath $sdlJava |
    Where-Object { $_.FullName -ne (Get-Item -LiteralPath $sdlSurface).FullName }).FullName
$sources += (Join-Path $patchedDir 'SDLSurface.java')
$sources += (Get-ChildItem -Recurse -Filter *.java -LiteralPath "$PSScriptRoot\java").FullName
if (Test-Path -LiteralPath "$work\gen") {
    $generated = Get-ChildItem -Recurse -Filter *.java -LiteralPath "$work\gen" -ErrorAction SilentlyContinue
    if ($generated) { $sources += $generated.FullName }
}

$sourceList = Join-Path $work 'sources.txt'
# Not Set-Content -Encoding UTF8: on Windows PowerShell 5.1 that writes a BOM,
# and javac reads it as part of the first filename.
[IO.File]::WriteAllLines($sourceList, $sources, (New-Object Text.UTF8Encoding $false))
# android.jar goes on the classpath, not the bootclasspath: javac 23 ignores
# -bootclasspath for -source 17 and only warns about it.
& javac -nowarn -source 17 -target 17 -classpath $androidJar `
    -d "$work\classes" "@$sourceList"
if ($LASTEXITCODE -ne 0) { throw 'javac failed' }

$classes = (Get-ChildItem -Recurse -Filter *.class -LiteralPath "$work\classes").FullName
$classList = Join-Path $work 'classes.txt'
[IO.File]::WriteAllLines($classList, $classes, (New-Object Text.UTF8Encoding $false))
& $d8 --release --lib $androidJar --output "$work\dex" "@$classList"
if ($LASTEXITCODE -ne 0) { throw 'd8 failed' }

# ---- assemble --------------------------------------------------------------
# extractNativeLibs="true" in the manifest, so the .so may be deflated.
$staging = Join-Path $work 'staging'
New-Item -ItemType Directory -Force -Path "$staging\lib\arm64-v8a" | Out-Null
Copy-Item -LiteralPath $NativeLibrary -Destination "$staging\lib\arm64-v8a\libmain.so"
foreach ($extra in $ExtraLibraries) {
    if (-not (Test-Path -LiteralPath $extra)) { throw "Not found: $extra" }
    Copy-Item -LiteralPath $extra -Destination "$staging\lib\arm64-v8a\"
}
Copy-Item -LiteralPath "$work\dex\classes.dex" -Destination "$staging\classes.dex"

$assembled = Join-Path $work 'assembled.apk'
Copy-Item -LiteralPath $unsigned -Destination $assembled
Push-Location $staging
try {
    # jar is the one zip tool guaranteed to sit beside javac, and it stores
    # entries at their path relative to the current directory.
    & jar uf $assembled classes.dex lib
    if ($LASTEXITCODE -ne 0) { throw 'jar update failed' }
} finally { Pop-Location }

$aligned = Join-Path $OutputDirectory 'mkw-quest.apk'
& $zipalign -p -f 4 $assembled $aligned
if ($LASTEXITCODE -ne 0) { throw 'zipalign failed' }

# ---- sign ------------------------------------------------------------------
# A debug key is all a sideload needs; it is generated once and kept out of the repo.
# One debug keystore per user, shared by every work directory: Android refuses to update an
# installed package with an APK signed by a different key (INSTALL_FAILED_UPDATE_INCOMPATIBLE),
# so a keystore per output directory forced an uninstall whenever the build moved.
$keystoreDir = Join-Path $env:LOCALAPPDATA 'mkw-quest'
[IO.Directory]::CreateDirectory($keystoreDir) | Out-Null
$keystore = Join-Path $keystoreDir 'debug.keystore'
if (-not (Test-Path -LiteralPath $keystore)) {
    & keytool -genkeypair -keystore $keystore -storepass android -keypass android `
        -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 `
        -dname 'CN=Android Debug,O=Android,C=US'
    if ($LASTEXITCODE -ne 0) { throw 'keytool failed' }
}
& $apksigner sign --ks $keystore --ks-pass pass:android --key-pass pass:android $aligned
if ($LASTEXITCODE -ne 0) { throw 'apksigner failed' }

Write-Host "APK: $aligned"
