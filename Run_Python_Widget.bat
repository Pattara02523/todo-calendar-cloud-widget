@echo off
chcp 65001 >nul
title Desktop Widget Always On Top
cd /d "%~dp0"

set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if exist "%PYW%" (
    start "" "%PYW%" "%~dp0desktop_widget_always_on_top.py"
    exit
)

where pythonw >nul 2>&1
if %errorlevel% equ 0 (
    start "" pythonw "%~dp0desktop_widget_always_on_top.py"
    exit
)

where python >nul 2>&1
if %errorlevel% equ 0 (
    start "" python "%~dp0desktop_widget_always_on_top.py"
    exit
)

echo [แจ้งเตือน] ไม่พบ Python ในเครื่อง
pause
