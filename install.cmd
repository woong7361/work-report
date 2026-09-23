@echo off
rem Double-click entry point. ASCII only: cmd reads this file with the machine's
rem code page, so the Korean text lives in the PowerShell side.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
rem Keep the window open so the result stays readable after a double-click.
pause
endlocal
