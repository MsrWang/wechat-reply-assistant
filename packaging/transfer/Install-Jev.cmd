@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-release.ps1" -PackagePath "%~dp0package\jev-chat-windows" -ExpectedVersion 0.1.2
set "JEV_EXIT=%errorlevel%"
pause
exit /b %JEV_EXIT%
