@echo off
REM GovWorkflows console: double-click to set up (first run) and launch.
REM Needs Python 3.11+ and, for the first run only, Node.js LTS.
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    where py >nul 2>nul && (set "PY=py -3") || (
        where python >nul 2>nul && (set "PY=python") || (
            echo Python 3.11 or newer is required: https://www.python.org/downloads/
            echo Tick "Add python.exe to PATH" during install, then run this again.
            pause
            exit /b 1
        )
    )
)

%PY% scripts\launch_console.py %*
if errorlevel 1 pause
