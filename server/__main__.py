"""Run CueLight with `python -m server [port]` — no uvicorn incantation needed."""
from __future__ import annotations

import sys

import uvicorn

from .main import _get_local_ip, app


def main() -> None:
    port = 8000
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print(f"Ignoring invalid port {sys.argv[1]!r}; using 8000")
    ip = _get_local_ip()
    banner = "\n".join([
        "",
        "  CueLight is running.",
        f"  Caller (stage manager):  http://{ip}:{port}/",
        f"  Operators join at:       http://{ip}:{port}/join",
        "  (devices must be on the same network)",
        "",
        "  Press Ctrl+C to stop.",
        "",
    ])
    print(banner, flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
