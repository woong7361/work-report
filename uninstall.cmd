@echo off
rem Double-click entry point. ASCII only (see install.cmd).
rem The confirmation is asked by uninstall.ps1 so it can be in the user's language.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
echo.
pause
endlocal
