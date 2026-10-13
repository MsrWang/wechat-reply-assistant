@echo off
setlocal
set "JEV_EXPERIENCE_EXE=%~dp0dist\releases\0.1.6\jev-chat-windows\jev-chat-windows.exe"
if not exist "%JEV_EXPERIENCE_EXE%" exit /b 1
start "" "%JEV_EXPERIENCE_EXE%"
exit /b 0
