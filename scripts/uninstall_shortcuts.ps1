<# Removes all JARVIS shortcuts (Desktop, Start Menu, auto-start). #>
$targets = @(
  (Join-Path ([Environment]::GetFolderPath("Desktop")) "JARVIS.lnk"),
  (Join-Path ([Environment]::GetFolderPath("Programs")) "JARVIS.lnk"),
  (Join-Path ([Environment]::GetFolderPath("Startup")) "JARVIS (auto-start).lnk")
)
foreach ($t in $targets) {
  if (Test-Path $t) { Remove-Item $t -Force; Write-Host "removed $t" }
}
Write-Host "JARVIS shortcuts removed."
