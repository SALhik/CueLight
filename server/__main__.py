"""Entry point for `python -m server`: start CueLight and print the join URLs.

Meant for non-technical users (see run.sh / run.bat) — equivalent to
`uvicorn server.main:app --host 0.0.0.0 --port 8000` but with a friendly
banner showing the addresses to open on the caller and operator devices.
"""
from __future__ import annotations

import socket
import sys

import uvicorn

HOST = "0.0.0.0"
PORT = 8000


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main() -> None:
    ip = _get_local_ip()
    print()
    print("=" * 56)
    print("  CueLight is starting…")
    print()
    print(f"  Caller (this iPad/laptop):  http://{ip}:{PORT}")
    print(f"  Operators join at:          http://{ip}:{PORT}/join")
    print(f"  If mDNS works on your LAN:  http://cuelight.local:{PORT}")
    print()
    print("  Keep this window open for the whole show.")
    print("  Press Ctrl+C to stop the server.")
    print("=" * 56)
    print()
    sys.stdout.flush()  # the banner must land even when stdout is piped
    uvicorn.run("server.main:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
