#!/bin/sh
# One-line CueLight launcher for macOS / Linux.
# Creates a virtualenv on first run, installs dependencies, starts the server.
set -e
cd "$(dirname "$0")"

PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python 3 is not installed. Get it from https://www.python.org/downloads/"
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "First run: setting up (this takes a minute)…"
  "$PY" -m venv .venv
fi

. .venv/bin/activate
pip install -q -r requirements.txt
exec python -m server
