@echo off
title Research Assistant
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
rem First start: installs Python and the libraries into this folder (later starts: a quick check only)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install.ps1"
if errorlevel 1 (
    echo.
    echo Setup didn't finish - see the message above. Check the internet connection, then start this file again.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" start.py %*
if errorlevel 1 pause
