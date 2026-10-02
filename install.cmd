@echo off
rem Double-click entry point. ASCII only: cmd reads this file with the machine's
rem code page, so the Korean text lives in the PowerShell side.
rem
rem A double-click installs with -Copy: the files are staged once in a folder
rem this tool owns and every agent gets a link to that, so this folder can be
rem deleted afterwards and updating means unpacking the new version anywhere
rem and double-clicking again. A linked install (this folder stays and edits
rem take effect at once) is for developing the skill, and is one command:
rem powershell -File install.ps1
setlocal
chcp 65001 >nul
cd /d "%~dp0"

rem PowerShell refuses a switch given twice, so only add it when it is absent.
rem Built-in FOR and IF /I on purpose: a PATH carrying Git for Windows shadows
rem find.exe with the Unix find, which answers a different question.
set "EXTRA=-Copy"
for %%a in (%*) do if /i "%%~a"=="-Copy" set "EXTRA="

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %EXTRA% %*
echo.
rem Keep the window open so the result stays readable after a double-click.
pause
endlocal
