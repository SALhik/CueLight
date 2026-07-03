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

# Or: friendly entry point (same server, prints the join URLs)
python -m server

# Non-technical bootstrap (creates venv, installs, launches): run.sh / run.bat

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

### OSC outbound

An OSC target is modeled as a virtual position (`PositionType.OSC`) that appears in the caller grid alongside human positions. Differences from human positions: no WebSocket, created from an OSC patch file (not by joining), STANDBY runs a probe instead of calling, GO fires an OSC message instead of waiting for a human ack.

**Data flow for OSC GO:**
1. Caller taps GO (or Master GO fires armed OSC positions)
2. `StateManager` detects `type == OSC`, calls `osc.fire(device, cue_number)`
3. `osc.py` builds the OSC message from `go_template` (with `{cue}` substitution) and sends via UDP/TCP
4. Returns `SENT` (reply received or open-loop) or `NO_REPLY` (timeout)
5. Server sends `osc_result` message to caller + `full_state` update
6. Frontend shows SENT/NO_REPLY for 2s, then clears

**Data flow for OSC STANDBY (probe):**
1. Caller taps STANDBY on an OSC column (or Master STANDBY includes armed OSC positions)
2. `osc.probe(device)` runs tiered: OSC ping reply > TCP port connect > unverified
3. Result stored in `pos.osc_probe` / `pos.osc_trust`, caller notified via `full_state`

**Key files:**
- `server/models.py` — `PositionType`, `OscProbeState`, `OscFireResult`, `OscDevice`, `OscPatch`
- `server/patch.py` — load/save/validate patch files from `patches/` directory
- `server/osc.py` — `fire()` and `probe()` with tight 400ms timeouts, fully async
- `server/state.py` — `load_patch()`, `unload_patch()`, OSC branches in `call_go`/`call_standby`/`go_armed`/`standby_armed`, ~5s heartbeat for confirmable devices
- `server/files.py` — `require_safe_filename()`: showfile/patch filenames must be plain `.json` basenames (no path traversal); enforced in load/save and returned as HTTP 400

**OSC positions are runtime-only:** they are NOT written to `state/snapshot.json`. On startup, the patch is reloaded from `patches/` by the stored `osc_patch_filename`. `_send_position()` is a no-op for OSC positions.

**OSC I/O never holds the lock:** action handlers mutate state under the lock (setting `PROBING`, clearing standby, resolving `{cue}` numbers), then hand the network I/O to a background task via `_spawn_osc_task()`. `_probe_and_record()` / `_fire_and_record()` run all fires/probes for a batch concurrently (`asyncio.gather`), then re-acquire the lock to record results and notify the caller. They must never be awaited while the lock is held. Human positions therefore get their `standby_called`/`go_called` immediately — a slow OSC device can't delay them — and the caller sees an interim `full_state` with `osc_probe: "probing"` before the settled result arrives.

### Position colors

Each position is auto-assigned a color from an 8-color palette (`COLOR_PALETTE` in `state.py`) when it joins or when an OSC patch is loaded. `_next_color()` picks the first palette color not already in use; if all 8 are taken it wraps around. The caller can override any position's color via the rename modal's swatch picker, which sends a `set_color` message.

Colors are stored in `Position.color` (a hex string like `"#5b8def"`), persisted in `snapshot.json`, and included in `to_dict()` / `full_state`. The palette avoids red, green, and yellow to not clash with standby/go/preset button states.

**Caller view:** the label is wrapped in a `<span class="pos-label-pill">` with an inline `background` style. **Position view:** the `.label-display` element in the bottom bezel gets `background` set via JS. The server sends `color` in the `joined` message and pushes `color_changed` to the position when the caller changes it.

### Persistence

`persistence.py` writes `state/snapshot.json` on every state change, debounced at 100ms. `save_state()` serializes immediately (so the timer thread never reads live state) and only the disk write is deferred. On startup, `load_state()` restores positions (marked disconnected), lock, password, cue index, color, problem flag/message, `auto_standby`, `osc_patch_filename`, `showfile_filename`, `show_start_time`, and `last_go_time`. The showfile and OSC patch contents are **not** stored in the snapshot — the lifespan calls `_restore_files()` in main.py to reload both from `showfiles/` and `patches/` by the stored filenames (patch first, so showfile arming includes OSC positions). `restore_showfile()` keeps the persisted cue index (clamped to the cue count) instead of resetting to 0, so a server restart mid-show resumes at the right cue. OSC positions are filtered out of the snapshot on write.

**EXIT and resume:** the EXIT button calls `wipe_state()`, which cancels any pending debounced write and renames the snapshot to `state/snapshot.bak` (not deletes). `GET /api/backup_info` reports whether a backup exists; `POST /api/resume_show` calls `StateManager.resume_show()`, which promotes the backup back via `restore_backup()`, keeps the current caller connection, closes live position sockets (their devices auto-reconnect and merge into the restored state), restores the show log, and then the endpoint reloads patch+showfile via `_restore_files()`. The caller UI offers resume on the post-EXIT screen and in Settings ("Previous Show" section).

### Show log

`server/showlog.py` — `ShowLog` keeps an in-memory list of `{time, event, position, cue, detail}` entries and appends each to `state/showlog.jsonl` (so it survives restarts; loaded back in `__init__`). `StateManager` owns one as `self.log` and records under the lock: button events (`standby_called`/`standby_acked`/`go_called`/`go_acked`, with the current cue sequence), `master_standby`/`master_go`, `cue_advanced`/`cue_jumped`, `osc_fired` (detail = sent/no_reply), lock/pause/rename/join/disconnect/showfile/patch events, flash-check confirmations, `problem_raised`/`problem_cleared` (detail = message / who cleared), and `show_started`. Auto-standby calls carry `detail="auto"`. OSC heartbeat probes are deliberately NOT logged (noise). `GET /api/showlog` serves JSON, `?format=csv` a CSV attachment. `exit_show()` records `show_ended` then `log.rotate()` (archive to `showlog.bak`); `resume_show()` calls `log.restore()`.

### Label uniqueness

Labels are unique (case-insensitive). Enforced at four points:
- `POST /api/check_label` — checked by join page before navigating
- `register_position()` in state.py — returns `"duplicate"` if a new client_id uses a taken label (includes OSC labels)
- `rename_position()` in state.py — returns `False` if the new name conflicts
- `load_patch()` in state.py — skips devices whose name collides with an existing human label
- Caller-side rename modal also validates client-side before sending

### WebSocket protocol

Three endpoints: `/ws/caller`, `/ws/position`, and `/ws/observer`. On connect, the client sends a JSON handshake with `client_id` (plus `label` for positions, `password` for observers). The server responds with role assignment and initial state. A caller reconnecting with the **same** `client_id` takes over from its stale socket (the old one is closed without broadcasting `caller_disconnected`); a different `client_id` is rejected while a caller is connected. Malformed messages (missing/wrong-typed fields) are ignored by both message loops rather than tearing down the connection.

**Observers are read-only:** any number may connect (`observer_ws` dict in StateManager); they receive every `full_state` the caller gets, built by `get_full_state_for_observer()` which blanks `password`. Everything an observer sends is ignored. If `password_enabled`, the handshake password is checked and rejected with `role_rejected`. `register_caller`/`unregister_caller` push a full_state to observers so they see `caller_connected` flip; the observer page then offers a manual TAKE OVER button that simply navigates to `/` (a vacant caller seat accepts any client_id). `full_state` includes `caller_connected` and `auto_standby` fields.

**Caller messages (client → server):** `standby`, `go`, `standby_armed`, `go_armed`, `reset_armed`, `toggle_arm`, `rename`, `set_color`, `lock`, `exit`, `set_password`, `load_showfile`, `unload_showfile`, `jump_to_cue`, `prev_cue`, `pause`, `set_auto_standby`, `flash_all`, `clear_flash`, `remove_position`, `load_patch`, `unload_patch`, `clear_problem` (with `client_id`), `start_show`

**Position messages (client → server):** `ack_standby`, `ack_go`, `ack_flash`, `rename`, `disconnect`, `pong`, `raise_problem` (optional `message`, capped at 60 chars server-side), `clear_problem`

**Server → position messages:** `joined`, `standby_called`, `go_called`, `flash`, `state_reset`, `lock_changed`, `label_changed`, `color_changed`, `cue_info`, `caller_disconnected`, `show_ended`, `removed`, `join_rejected`, `ping`, `health`, `problem_changed` (`problem`, `message`), `show_started` (`start_time`)

**Server → caller messages:** `role_assigned`, `role_rejected`, `full_state`, `osc_result`, `ping`, `error`

**HTTP API additions:** `GET /api/patches` (list), `GET /api/patch/{filename}`, `POST /api/patch/{filename}` (validate+save), `GET /api/showlog` (`?format=csv`), `GET /api/showreport` (HTML, `?format=csv`), `GET /api/backup_info`, `POST /api/resume_show`, `POST /api/csv/import`, `POST /api/csv/export`

### Problem signal (position → caller)

Operators raise a PROBLEM flag from the position console (bezel button opens an in-flow panel with big preset buttons NOT READY / PROP ISSUE / NEED SM plus optional free text). `raise_problem` sets `Position.problem = True` and `Position.problem_message` (server caps at `PROBLEM_MESSAGE_MAX = 60` chars); `clear_problem` clears it — sent by the caller (with `client_id`) or by the raising position itself. Both directions are echoed to the position as `problem_changed` and to the caller/observers via `full_state`. Raising is NOT gated on `locked` (operator communication matters most during a hold). OSC positions never carry problems.

The flag and message are **persisted** in the snapshot (and thus survive reconnects, restarts, and EXIT/resume). Raise/clear are logged as `problem_raised` (detail = message) / `problem_cleared` (detail = "by caller" / "by operator"). Caller UI: solid orange `⚠ PROBLEM` badge (deliberately NON-flashing so it never disturbs the calling rhythm) + orange column outline; tapping the badge opens a readout modal with a Clear button. The position's panel sits in the layout flow (it shrinks the cue buttons rather than covering them) and is auto-closed by an incoming standby/GO. If the position disconnects, the DISCONNECTED badge takes precedence but the orange outline remains.

### Show timer / START SHOW

`start_show` (caller message) sets `AppState.show_start_time` (ISO string, persisted; cleared by EXIT since EXIT resets AppState), logs `show_started` (detail = "restart" on re-press), and broadcasts a `show_started` message to all positions — the position page shows a transient 4s banner (`.show-banner`, `pointer-events: none`, floats over the header strip, never the cue buttons). `AppState.last_go_time` is stamped on every `call_go`/`go_armed` (persisted). Both fields ride in `full_state`; the caller's bottom bar renders a quiet monospace timer from them, with per-device checkboxes in Settings → Show Timer (elapsed / since last GO / clock; stored in `localStorage` `cuelight_timer_prefs`). The START SHOW button confirms before (re)starting.

### Post-show report

`server/showreport.py` computes a report **on demand** from `manager.log.entries` — nothing is persisted. `compute_report()` matches each `standby_called` to the next `standby_acked` per position (a `go_called` cancels a pending standby → counted as never-acked, mirroring the server's auto-clear), takes deltas between consecutive `master_go` events for time-between-GOs, counts auto vs manual standbys (`detail == "auto"`), sums joins/disconnects, and pairs `problem_raised`/`problem_cleared`. `GET /api/showreport` serves a self-contained HTML page (inline CSS, all user text escaped); `?format=csv` a flat `section,position,cue,metric,value` CSV. Exposed like `/api/showlog` (no password gate). Buttons live in caller Settings → Show Log.

### Operator alerts (pure frontend)

Position-only, opt-in, in `position.js`: the ALERT bezel button toggles beep+vibration on incoming `standby_called`/`go_called` (two short 880Hz beeps = standby, one long lower beep = GO; `navigator.vibrate` patterns where supported — Android only, iOS relies on the beep). WebAudio requires a user gesture, so the AudioContext is created/resumed on taps while alerts are on (same constraint-driven pattern as keepawake); the toggle tap itself unlocks and plays a confirmation blip. Persisted in `localStorage` (`cuelight_alerts`), off by default. No server involvement.

### Flash roll call

`flash_all` sets `Position.flash = "pending"` on every connected human position and sends them a `flash` message (OSC and disconnected positions are reset to `"none"`); the position shows a blinking overlay until tapped, which sends `ack_flash` → `flash = "confirmed"`. `clear_flash` resets all. `flash` is transient — serialized in `to_dict()` but never restored from the snapshot. The caller UI lives in Settings (Health list shows waiting/here markers and re-renders on every `full_state` while the modal is open).

### Auto-standby

`AppState.auto_standby` (persisted, off by default, toggled via `set_auto_standby`). In `go_armed`, when `_advance_cue()` actually advances (returns True) and the option is on, the newly-armed positions get standby immediately: humans with idle standby → `CALLED` + `standby_called` (logged with `detail="auto"`); armed OSC positions get a probe batch. Jump/prev never auto-call.

### Showfile CSV

`server/showcsv.py` — `cues_to_csv()` / `csv_to_cues()` (stdlib `csv`). Columns `sequence,scene,targets,note`; header required, order-free, unknown columns ignored; targets are `;`-separated `POSITION:CUE` pairs split on the **last** colon (labels may contain spaces). Import returns row-numbered errors and no cues on any error. Endpoints decode with `utf-8-sig` (Excel BOM). The editor's Import/Export CSV buttons call these endpoints so the dialect has a single implementation; `showfiles/example.csv` is the generated twin of `example.json`.

### Health monitoring

Server pings each position every 1s. Position echoes `pong` with the timestamp, which resets the missed-pong counter; 3 consecutive missed pongs = red. Server computes round-trip latency: >1s = yellow, >3s = red. When a position's health **tier** changes, the server pushes a `full_state` to the caller on its own (latency-only changes don't push). The caller sees per-position health in Settings; positions see their own health dot in the bottom bezel.

## Conventions

- **Backend:** Python 3.10+ (minimum, verified by the test suite), but 3.12 is the default — develop, test, and build on 3.12. Uses dataclasses (not Pydantic), `from __future__ import annotations` in every file. No type checker is configured but type hints are used throughout.
- **Frontend:** Vanilla JS in IIFEs, no modules/imports/bundler. Each page has its own `.js` file. CSS uses custom properties defined in `common.css`. Class toggling (`.classList.add/remove/toggle`) for state changes, not inline styles.
- **State serialization:** All model classes have `to_dict()` methods. The server sends dicts over WebSocket, never raw dataclass instances.
- **Async locking:** All `StateManager` mutation methods use `async with self._lock:`. Private helpers like `_advance_cue()` and `_arm_current_cue()` are called inside an already-held lock — they must never acquire it themselves.
- **Mobile-first concerns:** The app runs over HTTP on LAN. Never use APIs that require secure context (HTTPS) — this is why screen keep-awake uses the NoSleep technique (`static/js/keepawake.js`: hidden muted looping `static/keepawake.mp4`, started on first tap) instead of the Wake Lock API. Touch events need explicit handling alongside mouse events (see lock button). Use `100dvh` not `100vh` for mobile viewport. The position console's DIM button (off → dim → red) is a pure-frontend `pointer-events: none` overlay above everything (z-index 150 > lock's 100), persisted in `localStorage` — taps always pass through.
- **mDNS:** `main.py` advertises `cuelight.local` via `zeroconf` at startup (`_start_mdns()`, best-effort in a daemon thread — registration probes the network for seconds and must never block the event loop; any failure silently falls back to IP/QR). `/api/info` reports `mdns_host` ("" when inactive); the caller UI shows the name in Settings → Network and on the join-info screen. The ServiceInfo port is the default 8000 — the A-record (name → IP) is what matters for typed URLs.

## Testing

Tests are in `tests/test_cuelight.py`. They test via real WebSocket connections to a running server (integration tests, not mocks). The test harness starts a uvicorn server on port 8001 in a background thread, runs async test methods using `asyncio.run()`, and tears down after. Tests use the `websockets` library to connect as caller/positions and assert on the JSON messages received.

Always clean `state/snapshot.json` (plus `snapshot.bak`, `showlog.jsonl`, `showlog.bak`) before test runs to avoid state leakage — the suite's `_clean_state()` does this in setUp/tearDown of the module.
