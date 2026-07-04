@echo off
rem One-click CueLight launcher for Windows.
rem Creates a virtualenv on first run, installs dependencies, starts the server.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python 3 is not installed. Get it from https://www.python.org/downloads/
  echo Make sure to tick "Add Python to PATH" in the installer.
  pause
  exit /b 1
)

if not exist ".venv" (
  echo First run: setting up, this takes a minute...
  python -m venv .venv
  if errorlevel 1 (echo Failed to create the virtual environment. & pause & exit /b 1)
)

call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
if errorlevel 1 (echo Failed to install dependencies. Check your internet connection and try again. & pause & exit /b 1)
python -m server
pause
