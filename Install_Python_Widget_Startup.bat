@echo off
chcp 65001 >nul
title ตั้งค่าเปิด Python Widget บน Desktop และเปิดอัตโนมัติตอนเปิดคอม (Startup)

set "APP_DIR=%~dp0"
set "TARGET_BAT=%APP_DIR%Run_Python_Widget.bat"
set "WEB_BAT=%APP_DIR%Open_Widget_Right_Corner.bat"
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "DESKTOP_DIR=%USERPROFILE%\Desktop"

echo กำลังสร้างทางลัด Python Widget ไว้ที่หน้าจอ Desktop และตั้งค่าให้เปิดอัตโนมัติเมื่อเปิดเครื่อง...

powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP_DIR%\Python Widget (Always On Top).lnk'); $s.TargetPath = '%TARGET_BAT%'; $s.WorkingDirectory = '%APP_DIR%'; $s.WindowStyle = 7; $s.Save()"
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%STARTUP_DIR%\Python Widget (Always On Top).lnk'); $s.TargetPath = '%TARGET_BAT%'; $s.WorkingDirectory = '%APP_DIR%'; $s.WindowStyle = 7; $s.Save()"
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP_DIR%\Web Widget (Right Corner).lnk'); $s.TargetPath = '%WEB_BAT%'; $s.WorkingDirectory = '%APP_DIR%'; $s.WindowStyle = 7; $s.Save()"

echo.
echo [สำเร็จ] สร้างไอคอน "Python Widget (Always On Top)" และ "Web Widget (Right Corner)" บน Desktop พร้อมตั้งเปิดอัตโนมัติเรียบร้อยแล้ว!
pause
