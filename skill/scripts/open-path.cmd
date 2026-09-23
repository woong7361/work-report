@echo off
rem Click handler for the notification's private scheme.
rem A .cmd wrapper on purpose: pointing the protocol straight at powershell.exe
rem is not enough for a click coming from a toast to reach it.
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0open-path.ps1" "%~1"
