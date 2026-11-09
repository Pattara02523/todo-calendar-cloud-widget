@echo off
chcp 65001 >nul
title Desktop To-Do and Google Calendar Widget
cd /d "%~dp0"
set "HTML_PATH=%~dp0desktop_todo_calendar_widget.html"
if "%PORT%"=="" set "PORT=18234"
set "APP_URL=http://127.0.0.1:%PORT%"

REM Background Sync Server (pythonw -> where pythonw -> where python)
set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if exist "%PYW%" (
    start "" "%PYW%" "%~dp0desktop_sync_server.py"
) else (
    where pythonw >nul 2>&1
    if %errorlevel% equ 0 (
        start "" pythonw "%~dp0desktop_sync_server.py"
    ) else (
        where python >nul 2>&1
        if %errorlevel% equ 0 (
            start "" /min python "%~dp0desktop_sync_server.py"
        )
    )
)

REM Wait briefly (1-2s) for desktop_sync_server.py to listen on http://127.0.0.1:%PORT%
timeout /t 1 /nobreak >nul 2>&1

for /f "tokens=*" %%a in ('powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea.Width - 420"') do set X_POS=%%a
if "%X_POS%"=="" set X_POS=1480

if exist "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" (
    start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --app="%APP_URL%" --window-size=410,860 --window-position=%X_POS%,12
    exit
)
if exist "C:\Program Files\Microsoft\Edge\Application\msedge.exe" (
    start "" "C:\Program Files\Microsoft\Edge\Application\msedge.exe" --app="%APP_URL%" --window-size=410,860 --window-position=%X_POS%,12
    exit
)
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
    start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --app="%APP_URL%" --window-size=410,860 --window-position=%X_POS%,12
    exit
)
if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" (
    start "" "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" --app="%APP_URL%" --window-size=410,860 --window-position=%X_POS%,12
    exit
)
start "" "%APP_URL%"
