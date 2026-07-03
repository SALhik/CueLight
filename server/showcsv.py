from __future__ import annotations

import csv
import io
from typing import Any

REQUIRED_COLUMNS = ("sequence", "scene", "targets")
COLUMNS = ("sequence", "scene", "targets", "note")


def cues_to_csv(cues: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    for cue in cues:
        targets = ";".join(
            f"{t.get('position', '')}:{t.get('cue_number', '')}"
            for t in cue.get("targets", [])
        )
        writer.writerow([
            cue.get("sequence", ""),
            cue.get("scene", ""),
            targets,
            cue.get("note", ""),
        ])
    return buf.getvalue()


def csv_to_cues(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Parses spreadsheet CSV into showfile cue dicts.

    Header row is required; column order is free, unknown columns are
    ignored. Targets are ;-separated POSITION:CUE pairs — the split is on
    the last colon, so labels may contain spaces (e.g. "Fly 1:12a").
    Returns (cues, errors); cues is empty when there are errors.
    """
    text = text.lstrip("﻿")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        return [], ["Empty CSV"]

    col: dict[str, int] = {}
    for i, name in enumerate(rows[0]):
        col.setdefault(name.strip().lower(), i)
    missing = [name for name in REQUIRED_COLUMNS if name not in col]
    if missing:
        return [], [f"Missing column(s): {', '.join(missing)}"]

    cues: list[dict[str, Any]] = []
    errors: list[str] = []
    for n, row in enumerate(rows[1:], start=2):
        def cell(name: str) -> str:
            i = col.get(name)
            return row[i].strip() if i is not None and i < len(row) else ""

        seq_raw = cell("sequence")
        try:
            sequence = int(seq_raw)
        except ValueError:
            errors.append(f"Row {n}: invalid sequence {seq_raw!r}")
            continue

        targets: list[dict[str, str]] = []
        for token in cell("targets").split(";"):
            token = token.strip()
            if not token:
                continue
            if ":" not in token:
                errors.append(f"Row {n}: bad target {token!r} (expected POSITION:CUE)")
                continue
            position, cue_number = token.rsplit(":", 1)
            targets.append({
                "position": position.strip(),
                "cue_number": cue_number.strip(),
            })

        cues.append({
            "sequence": sequence,
            "scene": cell("scene"),
            "targets": targets,
            "note": cell("note"),
        })

    if errors:
        return [], errors
    return cues, []
