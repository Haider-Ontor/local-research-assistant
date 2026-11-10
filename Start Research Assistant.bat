@echo off
title Research Assistant
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" start.py %*
if errorlevel 1 pause
