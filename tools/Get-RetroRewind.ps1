[CmdletBinding()]
param(
    # The Retro Rewind 6 folder (the one that holds Binaries\Code.pul and version.txt).
    # Its parent is the install root: the update packages carry "RetroRewind6\..." and
    # "riivolution\..." at their top level and are unpacked into that parent. Must not be
    # inside this repository. Default: next to the builder's work directory.
    [string]$RetroRewindDirectory = (Join-Path $env:LOCALAPPDATA 'mkw-quest\RetroRewind6'),
    # Only report installed and latest version; exit code 0 = current, 10 = update
    # available, 11 = not installed. Nothing is downloaded.
    [switch]$CheckOnly,
    # Replace an existing installation with the full package (also needed when the
    # folder exists but carries no valid version.txt).
    [switch]$Reinstall,
    [string]$Server = 'https://update.rwfc.net/',
    # Where the downloaded packages are kept until they are unpacked.
    [string]$TempDirectory = (Join-Path $env:TEMP 'mkw-quest-retro-rewind')
)

# Downloads or updates Retro Rewind 6 without WheelWizard. Retro Rewind is a fan-made
# Riivolution pack (own tracks, own code); nothing here touches Nintendo's game data.
#
# The protocol is the one WheelWizard implements (source, read on 2026-09-08:
# https://github.com/TeamWheelWizard/WheelWizard, WheelWizard/Services/Endpoints.cs and
# WheelWizard/Features/CustomDistributions/RetroRewind.cs; server side
# https://github.com/Retro-Rewind-Team/update-server-api, src/manifest.rs):
#
#   <Server>RetroRewind/RetroRewindInstall.txt   body: URL of the newest full package (zip)
#   <Server>RetroRewind/RetroRewindVersion.txt   one line per delta zip, ascending:
#                                                "<version> <url> <sd-path> <description>"
#   <Server>RetroRewind/RetroRewindDelete.txt    one line per removed file:
#                                                "<version> </path/relative/to/install/root>"
#
# Install: download the full zip, unpack its "RetroRewind6/" and "riivolution/" trees into
# the install root, then run the update pass (a full package can lag the delta list).
# Update: installed version from RetroRewind6\version.txt; every delta line with a version
# above it is applied in file order; before the first zip, the deletions with
# installed < version <= target are applied (WheelWizard's order); version.txt is rewritten
# after each package. A version below 3.2.6 is reinstalled from the full package, as in
# WheelWizard. Deviations from WheelWizard: deletions and zip entries outside the
# "RetroRewind6" and "riivolution" trees are skipped instead of applied (the install root
# may hold other folders here), and an existing folder without a valid version.txt is
# never wiped without -Reinstall.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0
Add-Type -AssemblyName System.Net.Http
Add-Type -AssemblyName System.IO.Compression.FileSystem
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$UserAgent = 'mkw-quest/1.0'
$FolderName = 'RetroRewind6'
$XmlFolderName = 'riivolution'
$MinimumDeltaVersion = [version]'3.2.6'

$RetroRewindDirectory = [IO.Path]::GetFullPath($RetroRewindDirectory).TrimEnd('\')
$repoRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
if ($RetroRewindDirectory.StartsWith($repoRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "The Retro Rewind folder must not live inside this repository ($repoRoot)."
}
if ((Split-Path -Leaf $RetroRewindDirectory) -ne $FolderName) {
    throw "The folder must be named $FolderName (the packages unpack into that name): $RetroRewindDirectory"
}
$installRoot = Split-Path -Parent $RetroRewindDirectory
if (-not $Server.EndsWith('/')) { $Server += '/' }

# ---- HTTP ------------------------------------------------------------------------
$script:http = New-Object System.Net.Http.HttpClient
$script:http.Timeout = [TimeSpan]::FromHours(4)
$script:http.DefaultRequestHeaders.UserAgent.ParseAdd($UserAgent)

function Unwrap([System.Management.Automation.ErrorRecord]$Record) {
    $e = $Record.Exception
    while ($e -is [AggregateException] -and $e.InnerException) { $e = $e.InnerException }
    return $e.Message
}

function Get-Text([string]$Url) {
    try { return $script:http.GetStringAsync($Url).Result }
    catch { throw "GET $Url failed: $(Unwrap $_)" }
}

function Save-Download([string]$Url, [string]$Path) {
    [IO.Directory]::CreateDirectory((Split-Path -Parent $Path)) | Out-Null
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Force }
    try {
        $response = $script:http.GetAsync($Url, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).Result
    } catch { throw "GET $Url failed: $(Unwrap $_)" }
    try {
        if (-not $response.IsSuccessStatusCode) { throw "GET $Url returned HTTP $([int]$response.StatusCode)" }
        $total = $response.Content.Headers.ContentLength
        $in = $response.Content.ReadAsStreamAsync().Result
        $out = [IO.File]::Create($Path)
        try {
            $buffer = New-Object byte[] (1MB)
            $done = 0L; $lastReport = 0L
            while (($read = $in.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $out.Write($buffer, 0, $read)
                $done += $read
                if ($done - $lastReport -ge 100MB) {
                    $lastReport = $done
                    if ($total) { Write-Host ("  {0:N0} of {1:N0} MB" -f ($done / 1MB), ($total / 1MB)) }
                    else { Write-Host ("  {0:N0} MB" -f ($done / 1MB)) }
                }
            }
        } finally { $out.Dispose(); $in.Dispose() }
        if ($total -and $done -ne $total) { throw "Download of $Url ended after $done of $total bytes" }
    } finally { $response.Dispose() }
}

# ---- server lists ------------------------------------------------------------------
function Get-VersionList {
    $lines = (Get-Text ($Server + 'RetroRewind/RetroRewindVersion.txt')) -split "`n"
    $list = New-Object System.Collections.Generic.List[object]
    foreach ($line in $lines) {
        $parts = $line.Trim().Split(' ', 4)
        if ($parts.Count -lt 4) { continue }
        $v = $null
        if (-not [version]::TryParse($parts[0], [ref]$v)) { continue }
        # Old lists carried the plain-http address of the server; WheelWizard rewrites it.
        $url = $parts[1].Replace('http://update.rwfc.net:8000/', 'https://update.rwfc.net/')
        $list.Add([pscustomobject]@{ Version = $v; Url = $url; Description = $parts[3] })
    }
    if ($list.Count -eq 0) { throw 'The version list from the server is empty.' }
    return $list
}

function Get-DeletionList {
    $lines = (Get-Text ($Server + 'RetroRewind/RetroRewindDelete.txt')) -split "`n"
    $list = New-Object System.Collections.Generic.List[object]
    foreach ($line in $lines) {
        $parts = $line.Trim().Split(' ', 2)
        if ($parts.Count -lt 2 -or [string]::IsNullOrWhiteSpace($parts[1])) { continue }
        $v = $null
        if (-not [version]::TryParse($parts[0], [ref]$v)) { throw "Unreadable version in the deletion list: $line" }
        $list.Add([pscustomobject]@{ Version = $v; Path = $parts[1].Trim() })
    }
    return $list
}

# ---- local state ---------------------------------------------------------------------
$versionFile = Join-Path $RetroRewindDirectory 'version.txt'
function Get-InstalledVersion {
    if (-not (Test-Path -LiteralPath $versionFile)) { return $null }
    $text = [IO.File]::ReadAllText($versionFile).Trim()
    if ($text -notmatch '^\d+\.\d+\.\d+$') { return $null }
    return [version]$text
}
function Set-InstalledVersion([version]$Version) {
    # Same form WheelWizard writes: "major.minor.patch", no newline, UTF-8 without BOM.
    [IO.File]::WriteAllText($versionFile, $Version.ToString())
}

# ---- safe paths and unpacking ----------------------------------------------------------
# Resolves a server-provided relative path inside the install root and only accepts the two
# trees the packages own. Returns '' for anything else (logged by the caller).
function Resolve-PackPath([string]$Relative) {
    $rel = $Relative.Replace('\', '/').TrimStart('/')
    if ($rel -eq '' -or $rel.Contains(':') -or ($rel.Split('/') -contains '..') -or ($rel.Split('/') -contains '.')) { return '' }
    $top = $rel.Split('/')[0]
    if ($top -ne $FolderName -and $top -ne $XmlFolderName) { return '' }
    $full = [IO.Path]::GetFullPath((Join-Path $installRoot ($rel.Replace('/', '\'))))
    if (-not $full.StartsWith($installRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { return '' }
    return $full
}

function Expand-Pack([string]$ZipPath) {
    $archive = [IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $written = 0; $skipped = 0
        foreach ($entry in $archive.Entries) {
            $name = $entry.FullName.Replace('\', '/')
            if ($name.EndsWith('/') -or $name.EndsWith('desktop.ini')) { continue }
            $target = Resolve-PackPath $name
            if ($target -eq '') { $skipped++; continue }
            [IO.Directory]::CreateDirectory((Split-Path -Parent $target)) | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
            $written++
        }
        Write-Host "  $written files unpacked$(if ($skipped) { ", $skipped entries outside $FolderName/$XmlFolderName skipped" })"
    } finally { $archive.Dispose() }
}

# The version the full package declares, read from the zip before anything is replaced.
function Get-PackVersion([string]$ZipPath) {
    $archive = [IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $entry = $archive.GetEntry("$FolderName/version.txt")
        if (-not $entry) { $entry = $archive.GetEntry("$FolderName\version.txt") }
        if (-not $entry) { throw "The full package has no $FolderName/version.txt" }
        $reader = New-Object IO.StreamReader($entry.Open())
        try { $text = $reader.ReadToEnd().Trim() } finally { $reader.Dispose() }
        if ($text -notmatch '^\d+\.\d+\.\d+$') { throw "The full package's version.txt is unreadable: '$text'" }
        return [version]$text
    } finally { $archive.Dispose() }
}

# ---- steps ----------------------------------------------------------------------------
function Install-Full {
    $url = (Get-Text ($Server + 'RetroRewind/RetroRewindInstall.txt')).Trim()
    if ($url -notmatch '^https?://') { throw "RetroRewindInstall.txt did not return a URL: '$url'" }
    $zip = Join-Path $TempDirectory (Split-Path -Leaf ([uri]$url).AbsolutePath)
    Write-Host "Downloading the full package $url"
    Save-Download $url $zip
    $packVersion = Get-PackVersion $zip
    Write-Host "Package version $packVersion; unpacking into $installRoot"
    if (Test-Path -LiteralPath $RetroRewindDirectory) { Remove-Item -LiteralPath $RetroRewindDirectory -Recurse -Force }
    $xml = Join-Path $installRoot "$XmlFolderName\$FolderName.xml"
    if (Test-Path -LiteralPath $xml) { Remove-Item -LiteralPath $xml -Force }
    [IO.Directory]::CreateDirectory($installRoot) | Out-Null
    Expand-Pack $zip
    Remove-Item -LiteralPath $zip -Force
    if (-not (Get-InstalledVersion)) { throw "After unpacking, $versionFile is missing or unreadable." }
}

function Apply-Deltas([version]$Installed, $Versions) {
    $updates = @($Versions | Where-Object { $_.Version -gt $Installed })
    if ($updates.Count -eq 0) { return }
    $target = $updates[-1].Version
    Write-Host "Updating $Installed -> $target ($($updates.Count) package(s))"

    $deletions = @(Get-DeletionList | Where-Object { $_.Version -gt $Installed -and $_.Version -le $target } | Sort-Object Version)
    $removed = 0
    foreach ($d in $deletions) {
        $path = Resolve-PackPath $d.Path
        if ($path -eq '') { continue } # SD-card leftovers such as "/6.12.7.zip", or outside the pack trees
        if (Test-Path -LiteralPath $path -PathType Leaf) { Remove-Item -LiteralPath $path -Force; $removed++ }
        elseif (Test-Path -LiteralPath $path -PathType Container) { Remove-Item -LiteralPath $path -Recurse -Force; $removed++ }
    }
    if ($deletions.Count) { Write-Host "  $removed of $($deletions.Count) listed deletions applied (the rest did not exist here)" }

    $n = 0
    foreach ($u in $updates) {
        $n++
        $zip = Join-Path $TempDirectory (Split-Path -Leaf ([uri]$u.Url).AbsolutePath)
        Write-Host "[$n/$($updates.Count)] $($u.Version): $($u.Url)"
        Save-Download $u.Url $zip
        Expand-Pack $zip
        Remove-Item -LiteralPath $zip -Force
        Set-InstalledVersion $u.Version
    }
}

# ---- main ----------------------------------------------------------------------------
try {
    $installed = Get-InstalledVersion
    $versions = Get-VersionList
    $latest = $versions[-1].Version

    if ($CheckOnly) {
        if (-not $installed) { Write-Host "Retro Rewind is not installed at $RetroRewindDirectory (latest: $latest)"; exit 11 }
        if ($installed -lt $latest) { Write-Host "Retro Rewind $installed installed, $latest available"; exit 10 }
        Write-Host "Retro Rewind $installed is current"; exit 0
    }

    if ($installed -and -not $Reinstall -and $installed -ge $MinimumDeltaVersion) {
        Write-Host "Installed: Retro Rewind $installed at $RetroRewindDirectory (latest: $latest)"
        Apply-Deltas $installed $versions
    } else {
        if (-not $installed -and (Test-Path -LiteralPath $RetroRewindDirectory) -and -not $Reinstall -and
            @(Get-ChildItem -LiteralPath $RetroRewindDirectory -Force | Select-Object -First 1).Count) {
            throw "$RetroRewindDirectory exists but has no valid version.txt. Pass -Reinstall to replace it with the full package."
        }
        if ($installed) { Write-Host "Reinstalling Retro Rewind $installed from the full package" }
        else { Write-Host "Installing Retro Rewind into $RetroRewindDirectory" }
        Install-Full
        Apply-Deltas (Get-InstalledVersion) $versions
    }

    # Final check: version and mod code present.
    $final = Get-InstalledVersion
    $codePul = Join-Path $RetroRewindDirectory 'Binaries\Code.pul'
    if (-not $final) { throw "$versionFile is missing or unreadable after the update." }
    if (-not (Test-Path -LiteralPath $codePul)) { throw "$codePul is missing after the update." }
    if ($final -lt $latest) { throw "Retro Rewind is at $final, but the server lists $latest; the update did not complete." }
    Write-Host "Retro Rewind $final is current at $RetroRewindDirectory"
} finally {
    $script:http.Dispose()
    if (Test-Path -LiteralPath $TempDirectory) { Remove-Item -LiteralPath $TempDirectory -Recurse -Force -ErrorAction SilentlyContinue }
}
