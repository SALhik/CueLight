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
)

call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
python -m server
pause
