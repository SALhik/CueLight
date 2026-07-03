from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .persistence import STATE_DIR

SHOWLOG_PATH = STATE_DIR / "showlog.jsonl"
SHOWLOG_BACKUP_PATH = STATE_DIR / "showlog.bak"

CSV_COLUMNS = ["time", "event", "position", "cue", "detail"]


class ShowLog:
    """Append-only timestamped event log for the running show.

    Entries live in memory for fast serving and are appended to
    state/showlog.jsonl one line at a time, so the log survives a server
    restart. EXIT archives the log to showlog.bak alongside the state
    snapshot; resuming a show brings it back.
    """

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = _read_entries(SHOWLOG_PATH)

    def record(self, event: str, position: str = "", cue: str = "", detail: str = "") -> None:
        entry = {
            "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "event": event,
            "position": position,
            "cue": cue,
            "detail": detail,
        }
        self.entries.append(entry)
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            with SHOWLOG_PATH.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def to_csv(self) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(CSV_COLUMNS)
        for e in self.entries:
            writer.writerow([e.get(k, "") for k in CSV_COLUMNS])
        return buf.getvalue()

    def rotate(self) -> None:
        """EXIT: archive the current log next to snapshot.bak and start fresh."""
        self.entries = []
        if SHOWLOG_PATH.exists():
            SHOWLOG_PATH.replace(SHOWLOG_BACKUP_PATH)

    def restore(self) -> None:
        """Resuming a show: bring back the archived log."""
        if SHOWLOG_BACKUP_PATH.exists():
            SHOWLOG_BACKUP_PATH.replace(SHOWLOG_PATH)
        self.entries = _read_entries(SHOWLOG_PATH)


def _read_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        text = path.read_text()
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries
