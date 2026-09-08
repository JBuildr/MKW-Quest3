[CmdletBinding()]
param(
    # Created as a clone of patchzyy/Wiicompiled at the pinned commit; must not be
    # inside this repository (it will hold the user's translated game code).
    [Parameter(Mandatory)] [string]$Workspace,
    [string]$PatchDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) 'patches'),
    [string]$Repository = 'https://github.com/patchzyy/Wiicompiled',
    [string]$Commit = 'e6f9b21'
)

# Standalone work copy: no WiiCompiled PC installation needed. The clone carries
# runtime/, aurora-main/, projects/ and translator/ in the same layout as the
# installer's BuildWorkspace, so patches 0001 and 0003 apply unchanged, and 0002
# patches the translator that is built from the same tree.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0

$Workspace = [IO.Path]::GetFullPath($Workspace)
$repoRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
if ($Workspace.StartsWith($repoRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "The work copy must not live inside this repository ($repoRoot)."
}
foreach ($tool in @('git', 'dotnet')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool is required." }
}

if (-not (Test-Path -LiteralPath (Join-Path $Workspace '.git'))) {
    if (Test-Path -LiteralPath $Workspace) { throw "$Workspace exists but is not a git clone." }
    Write-Host "Cloning $Repository ..."
    & git clone --quiet $Repository $Workspace
    if ($LASTEXITCODE -ne 0) { throw 'git clone failed' }
}
& git -C $Workspace checkout --quiet $Commit
if ($LASTEXITCODE -ne 0) { throw "git checkout $Commit failed" }

# Fix AA: 1 MiB host stack per guest fiber. Newer WiiCompiled
# versions already ship this line, the pinned commit does not; a text replacement
# works on both where a patch hunk could not. Runs before 0003, whose
# fiber_manager.cpp hunk expects the 1 MiB line.
$fiberManager = Join-Path $Workspace 'runtime\src\fiber_manager.cpp'
if (Test-Path -LiteralPath $fiberManager) {
    $text = [IO.File]::ReadAllText($fiberManager)
    if ($text.Contains('constexpr size_t kHostStackSize = 64 * 1024;')) {
        [IO.File]::WriteAllText($fiberManager, $text.Replace('constexpr size_t kHostStackSize = 64 * 1024;', 'constexpr size_t kHostStackSize = 1024 * 1024;'))
        Write-Host 'Applied Fix AA (fiber host stack 1 MiB)'
    }
}

$patchOrder = @('0001-android-buildworkspace.patch', '0002-translator-target-os.patch', '0003-quest-adreno-workaround.patch')
foreach ($patch in $patchOrder) {
    $path = Join-Path $PatchDirectory $patch
    if (-not (Test-Path -LiteralPath $path)) { throw "Patch not found: $path" }
    # git reports the expected "patch failed" on stderr; under Windows PowerShell 5.1 a
    # redirected stderr line becomes a terminating error while $ErrorActionPreference is
    # Stop, so the check runs with Continue.
    $ErrorActionPreference = 'Continue'
    & git -C $Workspace apply --check --reverse --ignore-whitespace $path 2>$null
    $alreadyApplied = $LASTEXITCODE -eq 0
    $ErrorActionPreference = 'Stop'
    if ($alreadyApplied) { Write-Host "Already applied: $patch"; continue }
    $ErrorActionPreference = 'Continue'
    & git -C $Workspace apply --check --ignore-whitespace $path 2>$null
    $applies = $LASTEXITCODE -eq 0
    $ErrorActionPreference = 'Stop'
    if (-not $applies) {
        # Neither applied nor applicable: an older version of this patch is in the tree (the
        # repository moved on since the work copy was made). The work copy is a git clone, so
        # the files the patch touches go back to the pinned commit, Fix AA is redone where it
        # belongs, and the current patch goes on top.
        $files = @([IO.File]::ReadAllLines($path) | Where-Object { $_ -like '--- a/*' } | ForEach-Object { $_.Substring(6) } | Select-Object -Unique)
        Write-Host "$patch changed since it was applied here; resetting $($files.Count) file(s) to $Commit and re-applying."
        & git -C $Workspace checkout --quiet $Commit -- $files
        if ($LASTEXITCODE -ne 0) { throw "git checkout of the files of $patch failed" }
        # The reset also dropped what the earlier patches did to these files (0001 and 0003
        # both touch runtime/src/main.cpp): put their hunks for exactly these files back,
        # in patch order, before Fix AA and the current patch.
        foreach ($earlier in $patchOrder) {
            if ($earlier -eq $patch) { break }
            $earlierPath = Join-Path $PatchDirectory $earlier
            $shared = @([IO.File]::ReadAllLines($earlierPath) | Where-Object { $_ -like '--- a/*' } | ForEach-Object { $_.Substring(6) } | Where-Object { $files -contains $_ } | Select-Object -Unique)
            if ($shared.Count -eq 0) { continue }
            $include = @($shared | ForEach-Object { "--include=$_" })
            & git -C $Workspace apply --ignore-whitespace $include $earlierPath
            if ($LASTEXITCODE -ne 0) { throw "re-applying $earlier to $($shared -join ', ') failed" }
            Write-Host "  restored $earlier in $($shared -join ', ')"
        }
        if ($files -contains 'runtime/src/fiber_manager.cpp') {
            $text = [IO.File]::ReadAllText($fiberManager)
            [IO.File]::WriteAllText($fiberManager, $text.Replace('constexpr size_t kHostStackSize = 64 * 1024;', 'constexpr size_t kHostStackSize = 1024 * 1024;'))
        }
        & git -C $Workspace apply --check --ignore-whitespace $path
        if ($LASTEXITCODE -ne 0) { throw "$patch does not apply to $Commit" }
    }
    & git -C $Workspace apply --ignore-whitespace $path
    if ($LASTEXITCODE -ne 0) { throw "git apply failed for $patch" }
    Write-Host "Applied $patch"
}

$translatorExe = Join-Path $Workspace 'translator\src\Translator.Cli\bin\Release\net8.0\Translator.Cli.exe'
if (-not (Test-Path -LiteralPath $translatorExe)) {
    Write-Host 'Building the translator (dotnet build, Release)...'
    & dotnet build (Join-Path $Workspace 'translator\Translator.sln') -c Release --nologo -v quiet
    if ($LASTEXITCODE -ne 0) { throw 'dotnet build of the translator failed' }
}
Write-Host "Standalone work copy ready: $Workspace"
