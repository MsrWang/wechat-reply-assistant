@echo off
setlocal
rem Place this launcher at the project root after package verification.
set "JEV_EXPERIENCE_EXE=%~dp0dist\releases\0.1.3\jev-chat-windows\jev-chat-windows.exe"
if not exist "%JEV_EXPERIENCE_EXE%" (
    echo Jev 0.1.3 package is missing. Use the verified complete folder.
    pause
    exit /b 1
)
start "" "%JEV_EXPERIENCE_EXE%"
exit /b 0
