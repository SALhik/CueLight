"""Shared validation for user-supplied showfile/patch filenames."""
from __future__ import annotations

from pathlib import Path


def require_safe_filename(filename: str) -> None:
    """Reject anything but a plain .json basename (no path traversal)."""
    if (
        not filename.endswith(".json")
        or filename == ".json"
        or Path(filename).name != filename
    ):
        raise ValueError(f"Invalid filename: {filename!r}")
