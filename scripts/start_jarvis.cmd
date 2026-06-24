@echo off
title J.A.R.V.I.S
cd /d "%~dp0.."
rem `uv run jarvis` opens JARVIS in a chromeless app window with voice autoplay
rem enabled. If a server is already running, it just opens the window and exits.
uv run jarvis
