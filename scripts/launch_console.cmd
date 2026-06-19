@echo off
REM GovWorkflows Console Launcher
REM Double-click this file from the repo root to start the console.
REM It must live inside the repo; the repo root is computed automatically.

cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Python virtual environment not found.
    echo Please run the following from the repo root:
    echo.
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -e .
    echo.
    pause
    exit /b 1
)

.venv\Scripts\python.exe scripts\launch_console.py %*
