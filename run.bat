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
  if errorlevel 1 (echo Failed to create the virtual environment. & pause & exit /b 1)
)

.venv\Scripts\pip install --quiet -r requirements.txt
if errorlevel 1 (echo Failed to install dependencies. Check your internet connection and try again. & pause & exit /b 1)
.venv\Scripts\python -m server %*
pause
