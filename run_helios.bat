@echo off
cd /d "%~dp0"
start "" pythonw.exe main.py
if errorlevel 1 (
    python.exe main.py
    pause
)
