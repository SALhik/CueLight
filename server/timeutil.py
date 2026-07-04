from __future__ import annotations

from datetime import datetime


def now_iso() -> str:
    """Local-timezone ISO 8601 timestamp with millisecond precision."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")
