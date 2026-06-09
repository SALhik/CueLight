# CueLight — OSC Outbound Integration Spec

> Implementation brief for adding **outbound OSC** to CueLight. Read this top to bottom
> before writing code. It assumes familiarity with the existing architecture documented in
> `CLAUDE.md` — honor every convention there (async lock discipline, `to_dict()`
> serialization, dataclasses, vanilla-JS IIFEs, no secure-context browser APIs, `100dvh`,
> touch+mouse handlers, debounced persistence).

## 0. Goal & scope

Let a CueLight **caller** fire OSC at show-control gear (this venue: **grandMA3, QLab 5,
TheatreMix**, plus possible custom OSC software) as a natural part of calling cues.

**In scope:** outbound OSC fire on GO; an on-demand/standby *readiness probe*; per-device
configuration ("OSC patch"); caller-grid presentation of OSC devices; confirmation/failure
feedback where the device supports it.

**Explicitly out of scope (for now):** inbound OSC control of CueLight; piggybacking an OSC
fire onto a human position's GO (can be faked by arming both columns on the same cue — see
§13).

## 1. Core concept: an OSC device is a *virtual position*

An OSC target is modeled as a **position that is a machine instead of a human**. It appears
in the caller grid as a normal column with STANDBY / PRESET / GO, and rides the **existing**
arming / Master-GO / showfile auto-advance machinery unchanged. The differences from a human
position:

| Aspect | Human position | OSC position |
|---|---|---|
| Created by | iPad joins via QR (`register_position`) | Defined in an **OSC patch** file; injected when the patch loads |
| Backed by | a live WebSocket (`position_ws`) | no socket — server fires OSC for it |
| STANDBY/ack | operator taps to ack | server **probes** the device; reply drives the button |
| GO | operator taps GO on their console | server **sends the OSC fire**; reply confirms |
| Health | 1 s WS heartbeat | optional ~5 s OSC/TCP heartbeat |
| Identity | normal column | **cool-accent tint** + corner glyph when unverified |

**Binding contract:** an OSC device's `name` in the patch **is** its position label, and is
what showfile cue targets (`CueTarget.position`) match against (case-insensitive, same as
today). A cue targeting `SOUND` arms the OSC device named `SOUND`. Labels remain unique
across human + OSC positions (reuse existing duplicate enforcement) — one department, one
column.

## 2. Where it plugs into the codebase

Outbound OSC is **a fourth notification sink** hanging off `StateManager` mutations, beside
the existing `_send_position` / `_broadcast_positions` / `_notify_caller_full_state`
(`state.py:386-443`). You do **not** scatter OSC calls through the app.

| Concern | File | What changes |
|---|---|---|
| Position `type`, OSC fields | `server/models.py` | extend `Position`; add OSC config dataclasses |
| OSC patch load/parse/validate | new `server/patch.py` | mirror `server/showfile.py` |
| OSC send + reply/probe | new `server/osc.py` | python-osc client + reply listener + TCP probe |
| Emit on GO / probe on STANDBY | `server/state.py` | hook into `call_go`, `go_armed`, `call_standby`, `standby_armed`; new OSC-position branches |
| WS messages | `server/ws.py` | route patch load/unload, on-demand probe; pass OSC results to caller |
| HTTP API for patches | `server/main.py` | `GET/POST /api/patches`, mirror showfile endpoints |
| Grid rendering & states | `static/js/caller.js`, `static/css/caller.css` | OSC column visuals (§9, §11) |
| Patch editor UI | `static/editor.html`, `static/js/editor.js`, `static/css/editor.css` | device-table editor (§10) |
| Dependency | `requirements.txt` | add `python-osc>=1.8` |

## 3. Data model (`server/models.py`)

Add a position type and OSC config. Keep dataclasses + `from __future__ import annotations`
+ `to_dict()` on everything serialized.

```python
class PositionType(str, Enum):
    HUMAN = "human"
    OSC = "osc"

class OscProbeState(str, Enum):
    UNVERIFIED = "unverified"  # no ping configured — assumed ready (corner glyph)
    PROBING    = "probing"     # probe sent, awaiting reply (flashing)
    CONFIRMED  = "confirmed"   # reply received — reachable (solid)
    FAILED     = "failed"      # probe timed out (flashing, treated like "not acked")

class OscFireResult(str, Enum):
    NONE     = "none"
    SENT     = "sent"      # fire reply received (or open-loop fire dispatched)
    NO_REPLY = "no_reply"  # fired, no reply within timeout
```

Extend `Position`:

```python
type: PositionType = PositionType.HUMAN
# OSC-only runtime fields (ignored for HUMAN):
osc_probe: OscProbeState = OscProbeState.UNVERIFIED
osc_fire_result: OscFireResult = OscFireResult.NONE
osc_trust: str = "none"   # "osc_reply" | "tcp_port" | "none" — what a CONFIRMED actually proves
```

`Position.to_dict()` must include `type`, `osc_probe`, `osc_fire_result`, `osc_trust`.

OSC device **config** (loaded from the patch, not persisted in the snapshot):

```python
@dataclass
class OscDevice:
    name: str               # == position label / cue target
    ip: str
    port: int
    protocol: str = "udp"   # "udp" | "tcp"
    go_template: str = ""    # e.g. "/cue/{cue}/start" or "/go" (no placeholder = verbatim)
    go_args: list = field(default_factory=list)   # optional static OSC args for the fire
    ping_template: str = ""  # optional probe address; "" => UNVERIFIED + TCP-port fallback
    expect_reply: bool = True  # whether to wait for a reply to confirm fire/probe
    preset: str = "custom"   # "qlab5" | "grandma3" | "theatremix" | "custom" (editor hint only)
    # to_dict() for the patch editor / API

@dataclass
class OscPatch:
    name: str
    devices: list[OscDevice]
    filename: str = ""
```

## 4. The OSC patch file ("OSC patch")

- One JSON file per **patch profile**, stored in a new `patches/` directory (sibling of
  `showfiles/`). A patch = a named set of devices = the venue/rig endpoints.
- **Decoupled from showfiles on purpose:** cues travel with the production; IP/port are
  venue-specific (the QLab Mac gets a new IP every theatre). A showfile may reference a
  default patch by name (`"osc_patch": "<name>"`), and the caller can select/override which
  patch is active in Settings.
- Like the showfile, the patch is **not** stored in `state/snapshot.json` — it is reloaded
  from `patches/<file>.json` by name on startup/load (mirror showfile handling in
  `persistence.py` / `state.py`).

**Example `patches/mainstage.json`:**

```json
{
  "name": "Main Stage",
  "devices": [
    {
      "name": "SOUND",
      "preset": "qlab5",
      "ip": "192.168.1.50",
      "port": 53000,
      "protocol": "tcp",
      "go_template": "/cue/{cue}/start",
      "ping_template": "/version",
      "expect_reply": true
    },
    {
      "name": "LX",
      "preset": "grandma3",
      "ip": "192.168.1.20",
      "port": 8000,
      "protocol": "udp",
      "go_template": "/gma3/cmd",
      "go_args": ["Go+ Sequence 1"],
      "ping_template": "",
      "expect_reply": false
    },
    {
      "name": "BAND",
      "preset": "theatremix",
      "ip": "192.168.1.60",
      "port": 8000,
      "protocol": "udp",
      "go_template": "/TheatreMix/Go",
      "ping_template": "",
      "expect_reply": false
    }
  ]
}
```

`server/patch.py` provides `list_patches()`, `load_patch(filename) -> OscPatch`,
`save_patch(filename, data)`, `validate_patch(data) -> list[str]` — mirror
`server/showfile.py` exactly in shape and error style.

## 5. The unified GO template

When firing GO for an OSC position:

1. Resolve the **cue number** for this device from the current showfile cue target
   (`CueTarget.cue_number` for the matching label) — same lookup the grid already does for
   `cue_indicator` (`state.py:_update_cue_indicators`).
2. Substitute into `go_template`: replace `{cue}` with that number. **No `{cue}` placeholder
   ⇒ send the address verbatim** (device-GO model, e.g. QLab `/go`, MA3 a fixed command).
3. Append `go_args` (if any) as OSC arguments.
4. Send to `ip:port` over `protocol`.

This single model covers both worlds: per-cue fire (`/cue/{cue}/start` → `/cue/12/start`)
and fixed device-GO (`/go`). If no showfile is loaded, a template with `{cue}` should fire
with an empty/placeholder substitution and the caller log should note it (firing without a
cue number is allowed but unusual).

## 6. OSC sender + probe (`server/osc.py`)

Runs on the **same asyncio event loop** as the app (never a background thread — that would
break the `StateManager` lock model). Outbound-focused, but it must also **receive replies**
to close the loop.

Responsibilities:
- **Fire** (`async def fire(device, cue_number) -> OscFireResult`): build the message per §5,
  send it. If `expect_reply`: open/await a reply correlated by address with a **~400 ms
  timeout** → `SENT` on reply, `NO_REPLY` on timeout. If not `expect_reply`: dispatch and
  return `SENT` (open-loop; the fire still happened — see §8 wording).
- **Probe** (`async def probe(device) -> (OscProbeState, trust)`), tiered, strongest wins:
  1. `ping_template` set + `expect_reply` → send it, await reply (~400 ms). Reply ⇒
     `(CONFIRMED, "osc_reply")`; timeout ⇒ `(FAILED, "osc_reply")`.
  2. else if device reachable by **TCP connect** to `ip:port` (use `asyncio.open_connection`
     + timeout, no raw sockets / no privileges) ⇒ `(CONFIRMED, "tcp_port")`; refused/timeout
     ⇒ `(FAILED, "tcp_port")`.
  3. else (no ping, e.g. UDP-only) ⇒ `(UNVERIFIED, "none")` — assumed ready, never probed.
  - **Do NOT implement ICMP/host ping.** A host answering ping does not mean QLab is running;
    it is false confidence and needs raw-socket privileges. TCP-port probe is strictly better
    and privilege-free. (If a host-ping tier is ever added, it must be labeled weakest and
    never render as a clean green.)
- **Reply listening:** UDP devices need a bound UDP endpoint to catch replies addressed back
  to us; TCP devices read the reply on the same connection. Implement with python-osc’s
  async UDP server / `asyncio` transports. Correlate replies loosely by matching reply
  address to the sent command.
- **Background heartbeat (optional, ~5 s):** for devices where a probe can actually confirm
  (`osc_reply` or `tcp_port` tier only), re-probe every ~5 s to keep the header health dot
  honest (catches a mid-scene cable pull). **Slower than the 1 s human heartbeat**
  (`ws.py:11`) — be polite to consoles. UDP-only/unverified devices are not heartbeated.

All sends/probes are **best-effort and non-blocking**: wrap in try/except, never let a dead
device stall the held `StateManager` lock. Prefer firing and awaiting reply with a tight
timeout rather than any unbounded wait.

## 7. Loading / unloading the patch

- Add `StateManager.load_patch(patch: OscPatch)` and `unload_patch()` (async, lock-held,
  `_persist()`, `_notify_caller_full_state()`).
- On load: for each `OscDevice`, inject a `Position(type=OSC, label=device.name,
  connected=True, osc_probe=UNVERIFIED…)` into `state.positions`, keyed by a stable id such
  as `"osc:" + name.lower()`. Keep the `OscDevice` configs reachable (e.g. a
  `state.osc_devices: dict[id, OscDevice]`, not persisted). Reject/skip an OSC device whose
  name collides with an existing human label (reuse duplicate logic), and vice-versa: a human
  joining with a name that matches an OSC device is rejected as duplicate (existing path).
- On unload: remove OSC positions + their device configs.
- Patch selection can be folded into showfile load (if the showfile names a patch) and/or a
  Settings dropdown. The patch is reconstructed from `patches/` by filename on startup, never
  read from the snapshot (consistent with showfile handling).

## 8. StateManager integration (the emit/probe hooks)

Add OSC-aware branches; **all OSC I/O is awaited inside the already-held lock via the
`osc.py` helpers** (which themselves enforce tight timeouts). Mirror the existing notify
pattern.

- **`call_standby(client_id)` / `standby_armed()`** — if the position is OSC: run `probe()`,
  set `osc_probe`/`osc_trust` from the result, `_notify_caller_full_state()`. (Standby on an
  OSC column = readiness probe. Human positions unchanged.)
- **On-demand probe:** a caller STANDBY tap on an OSC column routes here and **always
  re-probes**, even if already `CONFIRMED`, so the SM can demand a fresh check right before a
  GO.
- **`call_go(client_id)` / `go_armed()`** — if the position is OSC: call `fire()`, set
  `osc_fire_result` (`SENT`/`NO_REPLY`), then notify. The GO **always dispatches** — never
  block it on probe/standby state (live override matters). Auto-advance still runs in
  `go_armed` exactly as today.
- **Transient result delivery:** `SENT` / `NO_REPLY` are momentary (the caller shows them for
  ~2 s then clears — §9). Deliver via a dedicated caller message
  `{"type": "osc_result", "client_id": ..., "result": "sent"|"no_reply"}` in addition to the
  `full_state` update, so the front end can drive the 2 s animation without the state getting
  "stuck". Reset `osc_fire_result` to `NONE` on the next mutation.
- `_send_position()` to an OSC position is a safe no-op (no socket) — guard or simply let the
  missing-ws path return.

## 9. Caller-side state machine & visuals

### STANDBY button (OSC column)
Reuse the existing standby classes (`caller.css:126-139` — dim red → flashing bright red →
solid bright red). Map probe state onto them:

| `osc_probe` | Look | Meaning |
|---|---|---|
| `PROBING` | flashing bright red | probe in flight |
| `FAILED` | **flashing** bright red (stays) | unreachable — treat exactly like a human who hasn't acked ("chase it") |
| `CONFIRMED` | solid bright red | reachable / ready |
| `UNVERIFIED` | solid bright red **+ corner glyph** | no ping configured — assumed ready, **not** confirmed |

- **Corner glyph:** a small hollow ring / `∅` in a corner of the standby button marking
  "unverified — fired blind." Keep the button face to the action word (muscle memory).
  Distinguish unverified-solid from confirmed-solid with the glyph, **not** a different red
  (red-vs-red is unreadable in a dim booth).

### GO button (OSC column)
- **Cool-accent identity:** OSC columns get a faint cool undertone (blue/cyan/violet — pick
  one, add a `--osc-accent` custom property in `common.css`) woven into **both** the standby
  and go buttons, so the whole column reads "automated" at a glance and the fire reads as
  "machine fired," not "human acked."
- **On GO:** button lights and **holds ~2 s** (not the instant human clear). During that 2 s
  show **`SENT`** text on the button face (readable at close range during the hold; color
  does the peripheral work).
- **On NO_REPLY:** the button **flashes red with `NO REPLY` for ~2 s**, then returns to
  idle. Wording matters: the fire *did* dispatch — `NO REPLY` means "couldn't confirm it
  landed," not "didn't fire." Never silently swallow this; a failed robot GO must be loud
  because no human will catch it.
- GO is never disabled by standby state. If fired into a `FAILED`/`PROBING` standby, that's
  allowed (override) — the NO_REPLY/SENT result still reports truthfully.

### Header
- Small `OSC` / `⚡` badge so the column is identifiable as a machine even apart from tint.
- Reuse the health-dot scheme for the ~5 s heartbeat where applicable; UDP-only/unverified
  devices show a neutral/“unverified” dot, not green.

Implement all state changes with `classList` toggles (no inline styles) per house style.
2 s timers live in `caller.js`, driven by the `osc_result` message and `full_state`.

## 10. Patch editor (front end)

Add OSC-patch editing to the existing editor (`/editor`) — either a new tab/section in
`editor.html` or a sibling page; match `editor.css` styling. A patch is a **table of
devices**, one row each:

- `name` (binds to label / cue target)
- `preset` selector: **QLab 5 / grandMA3 / TheatreMix / Custom** → prefills `go_template`,
  default `port`, `protocol`, `ping_template`, `expect_reply` from §App. (Prefill only — every
  field stays editable, since the unified template is the whole point.)
- `ip`, `port`, `protocol` (udp/tcp)
- `go_template` (with `{cue}` placeholder help text), optional `go_args`
- `ping_template` (optional) — with inline note: empty ⇒ device shows **unverified** at
  standby (auto TCP-port fallback if a port is reachable)
- `expect_reply` toggle
- a **Test** button per row → calls the probe and shows the resulting tier
  (`osc_reply` / `tcp_port` / unreachable / unverified)

Persist via `POST /api/patch/{filename}` (validate server-side, mirror
`/api/showfile/{filename}` in `main.py:124-131`). Settings modal (`caller.html`) gets a patch
**selector + current-patch label**, mirroring the existing showfile section
(`caller.html:66-79`).

## 11. WS / HTTP protocol additions

- **caller → server:** `load_patch {filename}`, `unload_patch`. (STANDBY on an OSC column
  reuses the existing `standby {client_id}` message — server detects `type==OSC` and probes;
  GO reuses `go {client_id}` / `go_armed`.)
- **server → caller:** existing `full_state` carries the new `Position` fields; add
  `osc_result {client_id, result}` for the transient SENT/NO_REPLY animation.
- **HTTP:** `GET /api/patches` (list), `GET /api/patch/{filename}`, `POST /api/patch/{filename}`
  (validate+save) — mirror the showfile endpoints.

Update the protocol section of `CLAUDE.md` once implemented.

## 12. Persistence

- New `patches/` dir holds patch JSON. Add to `.gitignore` only if showfiles are ignored;
  otherwise commit example patches alongside `showfiles/example.json`.
- OSC positions + device configs are runtime-reconstructed from the loaded patch — **not**
  written to `state/snapshot.json` (same rule as the showfile). On startup, if a patch was
  active, reload it from `patches/` by name. `wipe_state()` / EXIT clears them like everything
  else.

## 13. Testing (`tests/test_cuelight.py`)

The harness already drives a real server over WebSockets. Add:
- A throwaway UDP/TCP listener in the test to stand in for a device; assert CueLight sends the
  expected OSC address/args on GO (template substitution incl. `{cue}`).
- Probe tiers: a listener that replies → `CONFIRMED/osc_reply`; an open TCP port, no OSC reply
  → `CONFIRMED/tcp_port`; nothing listening → `FAILED`; no `ping_template` → `UNVERIFIED`.
- Fire confirmation: reply within timeout → `SENT`; silent listener → `NO_REPLY`.
- Master GO with an armed OSC position fires it (auto-advance still increments).
- Label collision between a patch device and a joining human is rejected.
Always clean `state/snapshot.json` before runs.

## 14. Build order (suggested phasing)

1. **Model + patch file + API + editor** — define `OscDevice`/`OscPatch`, `patch.py`,
   endpoints, editor table. No firing yet; just author and persist patches.
2. **Inject OSC positions** on patch load; render them in the grid as columns (cool tint +
   badge, static states). Verify Master/showfile arming includes them.
3. **`osc.py` fire** + `call_go`/`go_armed` hooks + GO `SENT`/`NO_REPLY` visuals (2 s).
4. **Probe** + `call_standby`/`standby_armed` hooks + on-demand re-probe + standby visuals +
   unverified glyph + TCP-port fallback.
5. **~5 s heartbeat** + header health dot.
6. Tests, then update `CLAUDE.md`.

## 15. Constraints checklist (do not violate)

- `async with self._lock:` for every `StateManager` mutation; private helpers
  (`_arm_current_cue`, etc.) run under an already-held lock — never re-acquire.
- OSC sender/listener live on the app's asyncio loop; **no background threads**, no raw
  sockets, no privileged operations.
- Every serialized object has `to_dict()`; never send raw dataclasses over the wire.
- Frontend: vanilla JS IIFEs, no modules/bundler; `classList` toggles, not inline styles;
  custom props in `common.css`; `100dvh`; touch **and** mouse handlers; no secure-context
  APIs (runs over HTTP on LAN).
- All OSC I/O is best-effort with tight timeouts so a dead device can never stall the lock or
  the show.

---

## Appendix — device starting points (VERIFY before trusting)

These are **suggested preset prefills only**. The system never hardcodes device behavior —
everything below is editable in the patch. Confirm exact addresses/ports against each
product's current OSC documentation for the installed version, and against the console-side
OSC configuration (especially grandMA3 and TheatreMix, which are configured on both ends).

| Device | Default port | Fire (GO) | Probe / reply | Notes |
|---|---|---|---|---|
| **QLab 5** | 53000 (UDP+TCP) | `/cue/{cue}/start` (specific cue) or `/go` (playhead) | Best closed loop: connect (may require `/connect "passcode"`), enable replies; fire returns `/reply/...` with `{"status":"ok"}` → use as `SENT`. Probe via a benign query (e.g. `/version`) or TCP connect to 53000. Optional `/updates 1` for richer feedback. | `expect_reply=true`, `protocol=tcp` recommended. Verify connect/passcode flow for QLab 5 specifically. |
| **grandMA3** | site-configured (e.g. 8000) | Typically command-line via the console's OSC config, e.g. `/gma3/cmd` with string arg `"Go+ Sequence 1"` (or an executor mapping). Addresses depend entirely on the MA3 OSC In setup. | No reliable per-command reply unless feedback is configured on the console → expect `tcp_port` or `UNVERIFIED` tier. | `expect_reply=false` to start. Both ends must be configured; coordinate with the LX programmer. |
| **TheatreMix** | site-configured | OSC transport commands (e.g. a `Go`/`Next` address) — confirm exact namespace in TheatreMix's OSC settings. | TheatreMix can emit OSC feedback on cue change; a reply-based confirm may be possible — verify. | Configure input port in TheatreMix; start `expect_reply=false` and upgrade if feedback is wired. |
| **Custom OSC software** | any | user-defined `go_template` (+ `{cue}`) and `go_args` | user-defined `ping_template`; falls back to TCP-port probe, else unverified | the generic path the whole design is built around. |

**Probe-trust wording for the UI** (carry into tooltips so a green never lies):
`osc_reply` = "app confirmed responsive" · `tcp_port` = "app's port is listening" ·
`none/unverified` = "not confirmed — firing blind."
