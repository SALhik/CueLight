from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Timer

from .models import AppState, ButtonState, HealthStatus, Position

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
SNAPSHOT_PATH = STATE_DIR / "snapshot.json"
BACKUP_PATH = STATE_DIR / "snapshot.bak"

_debounce_timer: Timer | None = None
DEBOUNCE_SECONDS = 0.1


def save_state(state: AppState) -> None:
    # Serialize now, on the event loop, so the timer thread never reads live
    # state that the event loop may be mutating concurrently.
    global _debounce_timer
    data = _serialize(state)
    if _debounce_timer is not None:
        _debounce_timer.cancel()
    _debounce_timer = Timer(DEBOUNCE_SECONDS, _write_snapshot, args=[data])
    _debounce_timer.daemon = True
    _debounce_timer.start()


def _serialize(state: AppState) -> dict:
    data = state.to_dict()
    # Filter out OSC positions — they are runtime-reconstructed from the loaded patch
    data["positions"] = {
        k: v for k, v in data["positions"].items()
        if v.get("type", "human") != "osc"
    }
    # The showfile is reloaded from showfiles/ by showfile_filename on startup
    data.pop("showfile", None)
    return data


def _write_snapshot(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SNAPSHOT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(SNAPSHOT_PATH)


def load_state() -> AppState:
    if not SNAPSHOT_PATH.exists():
        return AppState()
    try:
        data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return AppState()

    state = AppState()
    state.locked = data.get("locked", False)
    state.password_enabled = data.get("password_enabled", False)
    state.password = data.get("password", "")
    state.current_cue_index = data.get("current_cue_index", 0)
    state.paused = data.get("paused", False)
    state.auto_standby = data.get("auto_standby", False)
    state.osc_patch_filename = data.get("osc_patch_filename", "")
    state.showfile_filename = data.get("showfile_filename", "")
    # last_go_at is deliberately transient; the show clock survives restarts
    state.show_started_at = data.get("show_started_at", 0.0)
    if not state.show_started_at and data.get("show_start_time"):
        # Pre-rename snapshots stored the clock as an ISO string
        try:
            state.show_started_at = datetime.fromisoformat(
                data["show_start_time"]).timestamp()
        except (TypeError, ValueError):
            pass

    for cid, pdata in data.get("positions", {}).items():
        state.positions[cid] = Position(
            client_id=cid,
            label=pdata.get("label", ""),
            standby=ButtonState(pdata.get("standby", "idle")),
            go=ButtonState(pdata.get("go", "idle")),
            armed=pdata.get("armed", False),
            connected=False,
            health=HealthStatus.RED,
            latency_ms=0.0,
            cue_indicator=pdata.get("cue_indicator", ""),
            color=pdata.get("color", ""),
        )

    # Showfile is reloaded from disk by the showfile module, not from snapshot
    return state


def wipe_state() -> None:
    """EXIT: archive the snapshot instead of deleting it, so a mis-tapped
    EXIT mid-show can be undone via resume."""
    global _debounce_timer
    if _debounce_timer is not None:
        _debounce_timer.cancel()
        _debounce_timer = None
    if SNAPSHOT_PATH.exists():
        SNAPSHOT_PATH.replace(BACKUP_PATH)


def backup_exists() -> bool:
    return BACKUP_PATH.exists()


def backup_info() -> dict:
    if not BACKUP_PATH.exists():
        return {"exists": False}
    try:
        data = json.loads(BACKUP_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"exists": False}
    return {
        "exists": True,
        "showfile_filename": data.get("showfile_filename", ""),
        "position_count": len(data.get("positions", {})),
    }


def restore_backup() -> AppState | None:
    """Resume: promote the archived snapshot back and load it."""
    if not BACKUP_PATH.exists():
        return None
    BACKUP_PATH.replace(SNAPSHOT_PATH)
    return load_state()
