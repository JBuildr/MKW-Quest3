# Windows Forms front end for Invoke-QuestPipeline.ps1: pick the disc image,
# see what is missing, build and install with one click each. Start it through
# Quest-Builder.cmd in the repository root (it needs -STA).

Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$repoRoot = Split-Path -Parent $PSScriptRoot
$pipeline = Join-Path $PSScriptRoot 'Invoke-QuestPipeline.ps1'
$retroUpdater = Join-Path $PSScriptRoot 'Get-RetroRewind.ps1'
$logFile = Join-Path $env:TEMP 'mkw-quest-builder.log'

# ---- detection ---------------------------------------------------------------
function Find-First([string[]]$Candidates) {
    foreach ($candidate in $Candidates) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return ''
}
$defaults = [ordered]@{
    DiscImage      = ''
    RetroRewind    = Find-First @((Join-Path $env:LOCALAPPDATA 'mkw-quest\RetroRewind6'), "$env:APPDATA\CT-MKWII\RetroRewind\RetroRewind6")
    Sdk            = Find-First @("$env:ANDROID_HOME", "$env:ANDROID_SDK_ROOT", "$env:LOCALAPPDATA\Android\Sdk")
    Workspace      = Join-Path $env:LOCALAPPDATA 'mkw-quest\workspace'
    BuildWorkspace = Find-First @("$env:APPDATA\CT-MKWII\Recomp\Install\BuildWorkspace")
}

# Retro Rewind version check for the requirement list: installed version from version.txt,
# latest from the update server (same list Get-RetroRewind.ps1 reads; cached for the session,
# 5 s timeout so a missing connection only costs one wait).
$script:latestRetro = $null
$script:latestRetroChecked = $false
function Get-LatestRetroVersion {
    if (-not $script:latestRetroChecked) {
        $script:latestRetroChecked = $true
        try {
            $text = (Invoke-WebRequest -UseBasicParsing -Uri 'https://update.rwfc.net/RetroRewind/RetroRewindVersion.txt' `
                -Headers @{ 'User-Agent' = 'mkw-quest/1.0' } -TimeoutSec 5).Content
            $last = @(($text -split "`n") | Where-Object { $_.Trim() })[-1]
            $script:latestRetro = [version]$last.Trim().Split(' ')[0]
        } catch { $script:latestRetro = $null }
    }
    return $script:latestRetro
}
function Get-InstalledRetroVersion([string]$Directory) {
    $file = Join-Path $Directory 'version.txt'
    if (-not (Test-Path -LiteralPath $file)) { return $null }
    $text = [IO.File]::ReadAllText($file).Trim()
    if ($text -notmatch '^\d+\.\d+\.\d+$') { return $null }
    return [version]$text
}

function Get-Requirements([hashtable]$Values) {
    $items = New-Object System.Collections.Generic.List[object]
    $add = { param($name, $ok, $detail, $required) $items.Add([pscustomobject]@{ Name = $name; Ok = $ok; Detail = $detail; Required = $required }) }
    $standalone = -not [string]::IsNullOrWhiteSpace($Values.DiscImage)
    if ($standalone) {
        & $add 'Disc image (your own MKW PAL dump)' (Test-Path -LiteralPath $Values.DiscImage -PathType Leaf) $Values.DiscImage $true
    } else {
        & $add 'Disc image OR WiiCompiled BuildWorkspace' (Test-Path -LiteralPath (Join-Path $Values.BuildWorkspace 'generated')) $(if ($Values.BuildWorkspace) { $Values.BuildWorkspace } else { 'pick a disc image above' }) $true
    }
    $retroOk = [string]::IsNullOrWhiteSpace($Values.RetroRewind) -or (Test-Path -LiteralPath (Join-Path $Values.RetroRewind 'Binaries\Code.pul'))
    & $add 'Retro Rewind 6 (optional)' $retroOk $(if ($Values.RetroRewind) { $Values.RetroRewind } else { 'not set: base game only' }) $false
    if ($Values.RetroRewind) {
        $installed = Get-InstalledRetroVersion $Values.RetroRewind
        $latest = Get-LatestRetroVersion
        $detail = if (-not $installed) { 'not installed: click "Get / update Retro Rewind"' }
            elseif (-not $latest) { "$installed installed (update server not reachable)" }
            elseif ($installed -lt $latest) { "$installed installed, $latest available: click `"Get / update Retro Rewind`"" }
            else { "$installed (current)" }
        & $add 'Retro Rewind up to date' ([bool]$installed -and (-not $latest -or $installed -ge $latest)) $detail $false
    }
    $ndk = @(Get-ChildItem -LiteralPath (Join-Path $Values.Sdk 'ndk') -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '28.*' })
    & $add 'Android NDK 28.x' ($ndk.Count -gt 0) $(if ($ndk.Count) { $ndk[0].FullName } else { "$($Values.Sdk)\ndk" }) $true
    & $add 'Android build-tools 35.0.0' (Test-Path -LiteralPath (Join-Path $Values.Sdk 'build-tools\35.0.0\aapt2.exe')) "$($Values.Sdk)\build-tools\35.0.0" $true
    & $add 'Android platform android-34' (Test-Path -LiteralPath (Join-Path $Values.Sdk 'platforms\android-34\android.jar')) "$($Values.Sdk)\platforms\android-34" $true
    $cmake = Find-First @("$env:APPDATA\CT-MKWII\Recomp\Install\Toolkit\CMake\bin\cmake.exe")
    if (-not $cmake) {
        $sdkCmake = @(Get-ChildItem -LiteralPath (Join-Path $Values.Sdk 'cmake') -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending)
        if ($sdkCmake.Count) { $cmake = Join-Path $sdkCmake[0].FullName 'bin\cmake.exe' }
    }
    if (-not $cmake -and (Get-Command cmake -ErrorAction SilentlyContinue)) { $cmake = (Get-Command cmake).Source }
    & $add 'CMake + Ninja (SDK Manager "CMake" package)' ([bool]$cmake) $(if ($cmake) { $cmake } else { 'not found' }) $true
    $adb = Find-First @((Join-Path $Values.Sdk 'platform-tools\adb.exe'), "$env:USERPROFILE\Desktop\platform-tools\adb.exe")
    if (-not $adb -and (Get-Command adb -ErrorAction SilentlyContinue)) { $adb = (Get-Command adb).Source }
    & $add 'adb (needed to install)' ([bool]$adb) $adb $false
    foreach ($tool in @(@('javac', 'JDK (javac)'), @('git', 'Git'), @('dotnet', '.NET SDK 8+'))) {
        $cmd = Get-Command $tool[0] -ErrorAction SilentlyContinue
        & $add $tool[1] ([bool]$cmd) $(if ($cmd) { $cmd.Source } else { 'not on PATH' }) $true
    }
    $memoryGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
    & $add 'RAM (8 GB; the build picks its parallelism from the free memory)' ($memoryGB -ge 7) "$memoryGB GB" $false
    return $items
}

# ---- form ----------------------------------------------------------------------
$form = New-Object System.Windows.Forms.Form
$form.Text = 'mkw-quest builder'
$form.Size = New-Object System.Drawing.Size(880, 740)
$form.StartPosition = 'CenterScreen'
$form.MinimumSize = $form.Size
$form.Font = New-Object System.Drawing.Font('Segoe UI', 9)

$y = 12
$fields = @{}
function Add-PathRow([string]$Label, [string]$Key, [string]$Value, [bool]$File) {
    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = $Label; $lbl.Location = New-Object System.Drawing.Point(12, ($script:y + 4)); $lbl.Size = New-Object System.Drawing.Size(230, 20)
    $box = New-Object System.Windows.Forms.TextBox
    $box.Text = $Value; $box.Location = New-Object System.Drawing.Point(245, $script:y); $box.Size = New-Object System.Drawing.Size(520, 24)
    $box.Anchor = 'Top,Left,Right'
    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = '...'; $btn.Location = New-Object System.Drawing.Point(772, ($script:y - 1)); $btn.Size = New-Object System.Drawing.Size(70, 26)
    $btn.Anchor = 'Top,Right'
    # The handler runs in the script scope with $this as the button; the row's key and kind
    # travel in Tag. (A GetNewClosure() handler ran in its own scope, where $refresh did not
    # exist, and every "..." click ended in "The expression after & ... was not valid".)
    $btn.Tag = @{ Key = $Key; File = $File }
    $btn.Add_Click({
        $target = $fields[$this.Tag.Key]
        if ($this.Tag.File) {
            $dialog = New-Object System.Windows.Forms.OpenFileDialog
            $dialog.Filter = 'Wii disc images (*.iso;*.wbfs;*.rvz;*.wia;*.gcz;*.ciso;*.nkit.iso)|*.iso;*.wbfs;*.rvz;*.wia;*.gcz;*.ciso;*.nkit.iso|All files (*.*)|*.*'
            if ($dialog.ShowDialog() -eq 'OK') { $target.Text = $dialog.FileName; & $refresh }
        } else {
            $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
            if ($target.Text -and (Test-Path -LiteralPath $target.Text)) { $dialog.SelectedPath = $target.Text }
            if ($dialog.ShowDialog() -eq 'OK') { $target.Text = $dialog.SelectedPath; & $refresh }
        }
    })
    $form.Controls.AddRange(@($lbl, $box, $btn))
    $script:fields[$Key] = $box
    $script:y += 30
}
Add-PathRow 'Your Mario Kart Wii disc image (PAL)' 'DiscImage' $defaults.DiscImage $true
Add-PathRow 'Retro Rewind 6 directory (optional)' 'RetroRewind' $defaults.RetroRewind $false
Add-PathRow 'Android SDK' 'Sdk' $defaults.Sdk $false
Add-PathRow 'Work directory (created here)' 'Workspace' $defaults.Workspace $false
Add-PathRow 'BuildWorkspace (legacy, if no disc image)' 'BuildWorkspace' $defaults.BuildWorkspace $false

$reqList = New-Object System.Windows.Forms.ListView
$reqList.View = 'Details'; $reqList.FullRowSelect = $true; $reqList.HeaderStyle = 'Nonclickable'
$reqList.Location = New-Object System.Drawing.Point(12, $y); $reqList.Size = New-Object System.Drawing.Size(830, 215)
$reqList.Anchor = 'Top,Left,Right'
[void]$reqList.Columns.Add('Requirement', 290); [void]$reqList.Columns.Add('Status', 70); [void]$reqList.Columns.Add('Where', 450)
$form.Controls.Add($reqList)
$y += 225

$pushAssets = New-Object System.Windows.Forms.CheckBox
$pushAssets.Text = 'Push game data to the headset on install (first time only, several GB)'
$pushAssets.Location = New-Object System.Drawing.Point(12, $y); $pushAssets.Size = New-Object System.Drawing.Size(520, 24)
$form.Controls.Add($pushAssets)

$retroButton = New-Object System.Windows.Forms.Button
$retroButton.Text = 'Get / update Retro Rewind'; $retroButton.Location = New-Object System.Drawing.Point(12, ($y + 30)); $retroButton.Size = New-Object System.Drawing.Size(200, 30)
$buildButton = New-Object System.Windows.Forms.Button
$buildButton.Text = 'Build APK'; $buildButton.Location = New-Object System.Drawing.Point(542, ($y + 30)); $buildButton.Size = New-Object System.Drawing.Size(145, 30)
$buildButton.Anchor = 'Top,Right'
$installButton = New-Object System.Windows.Forms.Button
$installButton.Text = 'Install on Quest'; $installButton.Location = New-Object System.Drawing.Point(697, ($y + 30)); $installButton.Size = New-Object System.Drawing.Size(145, 30)
$installButton.Anchor = 'Top,Right'
$form.Controls.AddRange(@($retroButton, $buildButton, $installButton))
$y += 68

$log = New-Object System.Windows.Forms.TextBox
$log.Multiline = $true; $log.ScrollBars = 'Vertical'; $log.ReadOnly = $true; $log.WordWrap = $false
$log.Font = New-Object System.Drawing.Font('Consolas', 9)
$log.Location = New-Object System.Drawing.Point(12, $y); $log.Size = New-Object System.Drawing.Size(830, (720 - $y - 50))
$log.Anchor = 'Top,Bottom,Left,Right'
$form.Controls.Add($log)

$status = New-Object System.Windows.Forms.Label
$status.Text = 'Ready.'; $status.Location = New-Object System.Drawing.Point(12, 685); $status.Size = New-Object System.Drawing.Size(830, 20)
$status.Anchor = 'Bottom,Left,Right'
$form.Controls.Add($status)

# ---- behaviour -----------------------------------------------------------------
$refresh = {
    $values = @{}
    foreach ($key in $fields.Keys) { $values[$key] = $fields[$key].Text }
    $reqList.Items.Clear()
    $allOk = $true
    foreach ($item in (Get-Requirements $values)) {
        $row = New-Object System.Windows.Forms.ListViewItem($item.Name)
        [void]$row.SubItems.Add($(if ($item.Ok) { 'OK' } else { 'missing' }))
        [void]$row.SubItems.Add([string]$item.Detail)
        $row.ForeColor = if ($item.Ok) { [System.Drawing.Color]::DarkGreen } else { [System.Drawing.Color]::Firebrick }
        [void]$reqList.Items.Add($row)
        if (-not $item.Ok -and $item.Required) { $allOk = $false }
    }
    $buildButton.Enabled = $allOk
}
foreach ($box in $fields.Values) { $box.Add_Leave({ & $refresh }) }

$script:process = $null
$script:logOffset = 0
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 500
$timer.Add_Tick({
    if (Test-Path -LiteralPath $logFile) {
        $stream = [IO.File]::Open($logFile, 'Open', 'Read', 'ReadWrite')
        try {
            if ($stream.Length -gt $script:logOffset) {
                $stream.Position = $script:logOffset
                $reader = New-Object IO.StreamReader($stream, [Text.Encoding]::UTF8)
                $chunk = $reader.ReadToEnd()
                $script:logOffset = $stream.Position
                $log.AppendText($chunk)
            }
        } finally { $stream.Dispose() }
    }
    if ($script:process -and $script:process.HasExited) {
        $timer.Stop()
        $code = $script:process.ExitCode
        $script:process = $null
        $retroButton.Enabled = $true; $buildButton.Enabled = $true; $installButton.Enabled = $true
        $status.Text = if ($code -eq 0) { 'Finished successfully.' } else { "Failed (exit code $code); see the log above." }
        & $refresh
        if ($code -eq 0) { [System.Windows.Forms.MessageBox]::Show('Finished.', 'mkw-quest builder') | Out-Null }
    }
})

function Start-Script([string]$ScriptPath, [string[]]$Arguments, [string]$What) {
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $ScriptPath + '"')) + $Arguments
    if (Test-Path -LiteralPath $logFile) { Remove-Item -LiteralPath $logFile -Force }
    $script:logOffset = 0
    $log.Clear()
    $status.Text = $What
    $retroButton.Enabled = $false; $buildButton.Enabled = $false; $installButton.Enabled = $false
    # cmd redirects both streams into the log the timer tails; no console window.
    $command = 'powershell ' + ($arguments -join ' ') + ' > "' + $logFile + '" 2>&1'
    $script:process = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', $command) -WindowStyle Hidden -PassThru
    $timer.Start()
}

function Start-Pipeline([string[]]$ExtraArguments, [string]$What) {
    $arguments = @(
        '-Workspace', ('"' + $fields.Workspace.Text + '"'),
        '-Sdk', ('"' + $fields.Sdk.Text + '"')
    )
    if ($fields.DiscImage.Text) { $arguments += @('-DiscImage', ('"' + $fields.DiscImage.Text + '"')) }
    if ($fields.RetroRewind.Text) { $arguments += @('-RetroRewindDirectory', ('"' + $fields.RetroRewind.Text + '"')) }
    if ($fields.BuildWorkspace.Text) { $arguments += @('-BuildWorkspace', ('"' + $fields.BuildWorkspace.Text + '"')) }
    Start-Script $pipeline ($arguments + $ExtraArguments) "$What... (a first build takes about 20 minutes)"
}

$retroButton.Add_Click({
    # Empty field: the folder goes next to the work directory (never into the repository).
    if (-not $fields.RetroRewind.Text) { $fields.RetroRewind.Text = Join-Path (Split-Path -Parent $fields.Workspace.Text) 'RetroRewind6' }
    $script:latestRetroChecked = $false
    Start-Script $retroUpdater @('-RetroRewindDirectory', ('"' + $fields.RetroRewind.Text + '"')) 'Getting / updating Retro Rewind (a first download is about 2 GB)...'
})
$buildButton.Add_Click({ Start-Pipeline @() 'Building' })
$installButton.Add_Click({
    $extra = @('-InstallOnly')
    if ($pushAssets.Checked) { $extra += '-PushAssets' }
    Start-Pipeline $extra 'Installing'
})
$form.Add_FormClosing({ if ($script:process -and -not $script:process.HasExited) { $script:process.Kill() } })

& $refresh
[void]$form.ShowDialog()
