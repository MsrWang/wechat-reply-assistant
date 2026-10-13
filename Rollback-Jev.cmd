@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\rollback-release.ps1"
set "JEV_EXIT=%errorlevel%"
pause
exit /b %JEV_EXIT%
