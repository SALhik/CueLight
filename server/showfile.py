from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Cue, CueTarget, Showfile

SHOWFILES_DIR = Path(__file__).resolve().parent.parent / "showfiles"


def list_showfiles() -> list[str]:
    SHOWFILES_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(f.name for f in SHOWFILES_DIR.glob("*.json"))


def load_showfile(filename: str) -> Showfile:
    path = SHOWFILES_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Showfile not found: {filename}")
    data = json.loads(path.read_text())
    return _parse_showfile(data, filename)


def validate_showfile(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "show_name" not in data:
        errors.append("Missing 'show_name'")
    if "cues" not in data or not isinstance(data.get("cues"), list):
        errors.append("Missing or invalid 'cues' array")
        return errors
    sequences_seen: set[int] = set()
    for i, cue in enumerate(data["cues"]):
        if "sequence" not in cue:
            errors.append(f"Cue {i}: missing 'sequence'")
        elif cue["sequence"] in sequences_seen:
            errors.append(f"Cue {i}: duplicate sequence {cue['sequence']}")
        else:
            sequences_seen.add(cue["sequence"])
        if "scene" not in cue:
            errors.append(f"Cue {i}: missing 'scene'")
        if "targets" not in cue or not isinstance(cue.get("targets"), list):
            errors.append(f"Cue {i}: missing or invalid 'targets'")
        else:
            for j, t in enumerate(cue["targets"]):
                if "position" not in t:
                    errors.append(f"Cue {i}, target {j}: missing 'position'")
                if "cue_number" not in t:
                    errors.append(f"Cue {i}, target {j}: missing 'cue_number'")
    return errors


def save_showfile(filename: str, data: dict[str, Any]) -> None:
    SHOWFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOWFILES_DIR / filename
    path.write_text(json.dumps(data, indent=2))


def _parse_showfile(data: dict[str, Any], filename: str) -> Showfile:
    cues = []
    for c in data.get("cues", []):
        targets = [
            CueTarget(position=t["position"], cue_number=str(t["cue_number"]))
            for t in c.get("targets", [])
        ]
        cues.append(Cue(
            sequence=c["sequence"],
            scene=c.get("scene", ""),
            targets=targets,
            note=c.get("note", ""),
        ))
    cues.sort(key=lambda c: c.sequence)
    return Showfile(
        show_name=data.get("show_name", ""),
        version=data.get("version", 1),
        cues=cues,
        filename=filename,
    )
