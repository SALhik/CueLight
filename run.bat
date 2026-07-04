@echo off
rem One-command CueLight launcher (Windows). Double-click or run: run.bat [port]
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python 3 is required. Install it from https://www.python.org/downloads/
  echo ^(tick "Add python.exe to PATH" during install^)
  pause
  exit /b 1
)

if not exist .venv (
  echo First run: setting up, this takes a minute...
  python -m venv .venv
)

.venv\Scripts\pip install --quiet -r requirements.txt
.venv\Scripts\python -m server %*
pause
