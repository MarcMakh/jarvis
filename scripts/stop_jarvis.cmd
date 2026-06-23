@echo off
title Stop JARVIS
echo Stopping JARVIS server on port 7717...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 7717 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue; Write-Host ('stopped PID ' + $_.OwningProcess) }"
echo Done.
timeout /t 2 >nul
