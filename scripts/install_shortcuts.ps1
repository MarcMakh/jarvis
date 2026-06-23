<#
  Creates JARVIS shortcuts: Desktop, Start Menu, and (optionally) auto-start on login.
  Usage:
    powershell -ExecutionPolicy Bypass -File scripts\install_shortcuts.ps1            # all three
    powershell -ExecutionPolicy Bypass -File scripts\install_shortcuts.ps1 -NoAutoStart
#>
param([switch]$NoAutoStart)

$ErrorActionPreference = "Stop"
$proj    = (Resolve-Path "$PSScriptRoot\..").Path
$cmd     = Join-Path $proj "scripts\start_jarvis.cmd"
$vbs     = Join-Path $proj "scripts\start_jarvis_hidden.vbs"
$icon    = Join-Path $proj "static\jarvis.ico"
$ws      = New-Object -ComObject WScript.Shell

function New-Lnk($path, $target, $desc, $iconPath) {
  $lnk = $ws.CreateShortcut($path)
  $lnk.TargetPath       = $target
  $lnk.WorkingDirectory = $proj
  $lnk.Description       = $desc
  if (Test-Path $iconPath) { $lnk.IconLocation = $iconPath }
  $lnk.Save()
  Write-Host "  created $path"
}

Write-Host "Installing JARVIS shortcuts..."

# Desktop
$desktop = [Environment]::GetFolderPath("Desktop")
New-Lnk (Join-Path $desktop "JARVIS.lnk") $cmd "Launch JARVIS command center" $icon

# Start Menu (Programs)
$programs = [Environment]::GetFolderPath("Programs")
New-Lnk (Join-Path $programs "JARVIS.lnk") $cmd "Launch JARVIS command center" $icon

# Auto-start on login (hidden server, no browser)
if (-not $NoAutoStart) {
  $startup = [Environment]::GetFolderPath("Startup")
  New-Lnk (Join-Path $startup "JARVIS (auto-start).lnk") $vbs "Start JARVIS server hidden at login" $icon
  Write-Host "  auto-start ENABLED (runs hidden at login; stop via scripts\stop_jarvis.cmd)"
} else {
  Write-Host "  auto-start skipped (-NoAutoStart)"
}

Write-Host "Done."
