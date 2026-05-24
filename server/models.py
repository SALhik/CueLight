from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ButtonState(str, Enum):
    IDLE = "idle"
    CALLED = "called"
    ACKED = "acked"


class HealthStatus(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


@dataclass
class Position:
    client_id: str
    label: str
    standby: ButtonState = ButtonState.IDLE
    go: ButtonState = ButtonState.IDLE
    armed: bool = False
    connected: bool = True
    health: HealthStatus = HealthStatus.GREEN
    latency_ms: float = 0.0
    cue_indicator: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "label": self.label,
            "standby": self.standby.value,
            "go": self.go.value,
            "armed": self.armed,
            "connected": self.connected,
            "health": self.health.value,
            "latency_ms": self.latency_ms,
            "cue_indicator": self.cue_indicator,
        }


@dataclass
class CueTarget:
    position: str
    cue_number: str

    def to_dict(self) -> dict[str, str]:
        return {"position": self.position, "cue_number": self.cue_number}


@dataclass
class Cue:
    sequence: int
    scene: str
    targets: list[CueTarget]
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "scene": self.scene,
            "targets": [t.to_dict() for t in self.targets],
            "note": self.note,
        }


@dataclass
class Showfile:
    show_name: str
    version: int
    cues: list[Cue]
    filename: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "show_name": self.show_name,
            "version": self.version,
            "cues": [c.to_dict() for c in self.cues],
            "filename": self.filename,
        }


@dataclass
class AppState:
    positions: dict[str, Position] = field(default_factory=dict)
    caller_connected: bool = False
    caller_client_id: str | None = None
    locked: bool = False
    password_enabled: bool = False
    password: str = ""
    showfile: Showfile | None = None
    current_cue_index: int = 0
    paused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "caller_connected": self.caller_connected,
            "caller_client_id": self.caller_client_id,
            "locked": self.locked,
            "password_enabled": self.password_enabled,
            "password": self.password,
            "showfile": self.showfile.to_dict() if self.showfile else None,
            "current_cue_index": self.current_cue_index,
            "paused": self.paused,
        }
