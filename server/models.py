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


class PositionType(str, Enum):
    HUMAN = "human"
    OSC = "osc"


class OscProbeState(str, Enum):
    UNVERIFIED = "unverified"
    PROBING = "probing"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class OscFireResult(str, Enum):
    NONE = "none"
    SENT = "sent"
    NO_REPLY = "no_reply"


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
    type: PositionType = PositionType.HUMAN
    osc_probe: OscProbeState = OscProbeState.UNVERIFIED
    osc_fire_result: OscFireResult = OscFireResult.NONE
    osc_trust: str = "none"

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
            "type": self.type.value,
            "osc_probe": self.osc_probe.value,
            "osc_fire_result": self.osc_fire_result.value,
            "osc_trust": self.osc_trust,
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
class OscDevice:
    name: str
    ip: str
    port: int
    protocol: str = "udp"
    go_template: str = ""
    go_args: list = field(default_factory=list)
    ping_template: str = ""
    expect_reply: bool = True
    preset: str = "custom"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ip": self.ip,
            "port": self.port,
            "protocol": self.protocol,
            "go_template": self.go_template,
            "go_args": list(self.go_args),
            "ping_template": self.ping_template,
            "expect_reply": self.expect_reply,
            "preset": self.preset,
        }


@dataclass
class OscPatch:
    name: str
    devices: list[OscDevice]
    filename: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "devices": [d.to_dict() for d in self.devices],
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
    osc_patch_filename: str = ""

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
            "osc_patch_filename": self.osc_patch_filename,
        }
