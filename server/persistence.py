from __future__ import annotations

import json
from pathlib import Path
from threading import Timer

from .models import AppState, ButtonState, HealthStatus, Position

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
SNAPSHOT_PATH = STATE_DIR / "snapshot.json"

_debounce_timer: Timer | None = None
DEBOUNCE_SECONDS = 0.1


def save_state(state: AppState) -> None:
    global _debounce_timer
    if _debounce_timer is not None:
        _debounce_timer.cancel()
    _debounce_timer = Timer(DEBOUNCE_SECONDS, _write_state, args=[state])
    _debounce_timer.daemon = True
    _debounce_timer.start()


def _write_state(state: AppState) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data = state.to_dict()
    # Filter out OSC positions — they are runtime-reconstructed from the loaded patch
    data["positions"] = {
        k: v for k, v in data["positions"].items()
        if v.get("type", "human") != "osc"
    }
    tmp = SNAPSHOT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(SNAPSHOT_PATH)


def load_state() -> AppState:
    if not SNAPSHOT_PATH.exists():
        return AppState()
    try:
        data = json.loads(SNAPSHOT_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return AppState()

    state = AppState()
    state.locked = data.get("locked", False)
    state.password_enabled = data.get("password_enabled", False)
    state.password = data.get("password", "")
    state.current_cue_index = data.get("current_cue_index", 0)
    state.paused = data.get("paused", False)
    state.osc_patch_filename = data.get("osc_patch_filename", "")

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
        )

    # Showfile is reloaded from disk by the showfile module, not from snapshot
    return state


def wipe_state() -> None:
    if SNAPSHOT_PATH.exists():
        SNAPSHOT_PATH.unlink()
