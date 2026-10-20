@echo off
chcp 65001 >nul
title Desktop To-Do and Google Calendar Widget
cd /d "%~dp0"
set "HTML_PATH=%~dp0desktop_todo_calendar_widget.html"

REM Background Sync Server
set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if exist "%PYW%" (
    start "" "%PYW%" "%~dp0desktop_sync_server.py"
) else (
    where pythonw >nul 2>&1 && start "" pythonw "%~dp0desktop_sync_server.py"
)

for /f "tokens=*" %%a in ('powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea.Width - 420"') do set X_POS=%%a
if "%X_POS%"=="" set X_POS=1480

if exist "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" (
    start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --app="file:///%HTML_PATH%" --window-size=410,860 --window-position=%X_POS%,12
    exit
)
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
    start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --app="file:///%HTML_PATH%" --window-size=410,860 --window-position=%X_POS%,12
    exit
)
start "" "%HTML_PATH%"
