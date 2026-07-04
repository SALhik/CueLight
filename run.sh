#!/usr/bin/env bash
# One-command CueLight launcher (macOS / Linux).
# First run creates a private virtualenv and installs dependencies;
# after that it just starts the server. Usage: ./run.sh [port]
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "First run: setting up (this takes a minute)…"
  python3 -m venv .venv
fi

./.venv/bin/pip install --quiet -r requirements.txt
exec ./.venv/bin/python -m server "$@"
