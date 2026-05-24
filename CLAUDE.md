# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

CueLight is a browser-based theatre cue light system. A Python server runs on one machine; iPads and phones on the same LAN connect via browser. One device is the **Caller** (stage manager), the rest are **Positions** (operators). All real-time communication is over WebSockets.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
uvicorn server.main:app --host 0.0.0.0 --port 8000

# Run the test suite
python tests/test_cuelight.py

# Run a single test class
python -m pytest tests/test_cuelight.py::TestButtonStateMachine -v

# Run a single test
python -m pytest tests/test_cuelight.py::TestButtonStateMachine::test_standby_go_ack_cycle -v
```

There is no build step, no linter configured, no type checker configured. The frontend is vanilla JS.

## Architecture

### Server-side data flow

```
WebSocket message → ws.py handler → StateManager method → mutate AppState → persist → notify
```

Every state mutation follows this pattern:
1. `ws.py` receives a JSON message from a client WebSocket
2. Routes it to the appropriate `StateManager` method in `state.py`
3. The method acquires `self._lock` (asyncio Lock), mutates `self.state` (an `AppState` dataclass)
4. Calls `self._persist()` (debounced write to `state/snapshot.json`)
5. Sends targeted messages to affected positions via `_send_position()`
6. Broadcasts full state to the caller via `_notify_caller_full_state()`

The caller always receives a `full_state` message after every mutation — it's a full snapshot, not a delta. Positions receive only targeted messages (`standby_called`, `go_called`, `cue_info`, etc.).

### StateManager is the single source of truth

`StateManager` in `state.py` owns all mutable state. It holds:
- `self.state` (AppState) — the serializable state (positions, showfile, lock, password, cue index)
- `self.caller_ws` / `self.position_ws` — live WebSocket references (not persisted)
- `self._lock` — all mutations must acquire this

`ws.py` is a thin message router. `main.py` is a thin HTTP layer. Neither should hold state.

### Client identity

Clients generate a UUID stored in `localStorage` (`cuelight_client_id` for positions, `cuelight_caller_id` for caller). This survives page reloads. The server uses this ID as the key in `state.positions`. UUID generation uses a `Math.random()` fallback because `crypto.randomUUID()` requires a secure context and phones connect over HTTP on LAN.

### Button state machine

Both STANDBY and GO follow: `idle → called → acked → idle`. The transitions are:

| Event | STANDBY | GO |
|---|---|---|
| Caller fires | idle → called | — |
| Position taps (ack) | called → acked | called → idle |
| Caller fires GO | acked → idle (auto-clear) | idle → called |
| Position taps GO (ack) | — | called → idle |

The caller's column view mirrors the position's state — when a position acks, the caller's button dims to match.

### Showfile cue auto-advance

When a showfile is loaded:
1. `_arm_current_cue()` arms positions matching the current cue's targets (case-insensitive label match)
2. Master GO fires GO on all armed positions, then calls `_advance_cue()` which increments `current_cue_index` and re-arms for the next cue
3. `_broadcast_positions_cue_info()` sends each position its own cue number for the new cue
4. If paused, `_advance_cue()` is a no-op (manual buttons still work)

### Persistence

`persistence.py` writes `state/snapshot.json` on every state change, debounced at 100ms. On startup, `load_state()` restores positions (marked disconnected), lock, password, and cue index. The showfile is **not** stored in the snapshot — it's reloaded from `showfiles/` by filename. The EXIT button calls `wipe_state()` which deletes the snapshot.

### Label uniqueness

Labels are unique (case-insensitive). Enforced at three points:
- `POST /api/check_label` — checked by join page before navigating
- `register_position()` in state.py — returns `"duplicate"` if a new client_id uses a taken label
- `rename_position()` in state.py — returns `False` if the new name conflicts
- Caller-side rename modal also validates client-side before sending

### WebSocket protocol

Two endpoints: `/ws/caller` and `/ws/position`. On connect, the client sends a JSON handshake with `client_id` (and `label` for positions). The server responds with role assignment and initial state.

**Caller messages (client → server):** `standby`, `go`, `standby_armed`, `go_armed`, `reset_armed`, `toggle_arm`, `rename`, `lock`, `exit`, `set_password`, `load_showfile`, `unload_showfile`, `jump_to_cue`, `prev_cue`, `pause`, `remove_position`

**Position messages (client → server):** `ack_standby`, `ack_go`, `rename`, `disconnect`, `pong`

**Server → position messages:** `joined`, `standby_called`, `go_called`, `state_reset`, `lock_changed`, `label_changed`, `cue_info`, `caller_disconnected`, `show_ended`, `removed`, `join_rejected`, `ping`, `health`

**Server → caller messages:** `role_assigned`, `role_rejected`, `full_state`, `ping`, `error`

### Health monitoring

Server pings each position every 1s. Position echoes `pong` with the timestamp. Server computes round-trip latency: >1s = yellow, >3s = red, 3 missed pongs = red. The caller sees per-position health in Settings; positions see their own health dot in the bottom bezel.

## Conventions

- **Backend:** Python 3.11+, dataclasses (not Pydantic), `from __future__ import annotations` in every file. No type checker is configured but type hints are used throughout.
- **Frontend:** Vanilla JS in IIFEs, no modules/imports/bundler. Each page has its own `.js` file. CSS uses custom properties defined in `common.css`. Class toggling (`.classList.add/remove/toggle`) for state changes, not inline styles.
- **State serialization:** All model classes have `to_dict()` methods. The server sends dicts over WebSocket, never raw dataclass instances.
- **Async locking:** All `StateManager` mutation methods use `async with self._lock:`. Private helpers like `_advance_cue()` and `_arm_current_cue()` are called inside an already-held lock — they must never acquire it themselves.
- **Mobile-first concerns:** The app runs over HTTP on LAN. Never use APIs that require secure context (HTTPS). Touch events need explicit handling alongside mouse events (see lock button). Use `100dvh` not `100vh` for mobile viewport.

## Testing

Tests are in `tests/test_cuelight.py`. They test via real WebSocket connections to a running server (integration tests, not mocks). The test harness starts a uvicorn server on port 8001 in a background thread, runs async test methods using `asyncio.run()`, and tears down after. Tests use the `websockets` library to connect as caller/positions and assert on the JSON messages received.

Always clean `state/snapshot.json` before test runs to avoid state leakage.
