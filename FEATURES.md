# CueLight — Features Guide

CueLight is a browser-based theatre cue light system. One machine runs a Python
server; iPads and phones on the same LAN connect through a browser. One device
acts as the **Caller** (stage manager), and the others are **Positions**
(operators such as LX, Sound, Fly). All real-time signalling happens over
WebSockets, so cues appear instantly with no page reloads.

This document walks through every feature and how to use it.

---

## Getting started

### Starting the server

```bash
pip install -r requirements.txt
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

The server listens on all interfaces on port 8000. Find the host machine's LAN
IP (Settings shows it, or `/api/info` returns it) and share
`http://<ip>:8000` with the other devices.

### The three roles / pages

| URL | Page | Who uses it |
|---|---|---|
| `/` | Caller console | Stage manager (one device only) |
| `/join` → `/position` | Position console | Each operator |
| `/editor` | Showfile editor | Whoever prepares the cue list |

The **first** device to open `/` claims the Caller role. If a second device
opens `/` while a Caller is already connected, it is automatically redirected
to the join page to become a Position instead.

---

## Joining a show (Positions)

### Connecting as a Position

1. On the operator's phone, open `http://<ip>:8000/join` (or scan the Caller's
   QR code — see *Join info / QR code*).
2. Enter a **position name / label** (e.g. `LX`, `SND`, `Fly`). Max 16
   characters.
3. If the show is password-protected, enter the password (auto-filled when
   joining via QR code).
4. Tap **Join**. You land on the Position console at `/position`.

**How it works:** the label and a generated client ID are saved in
`localStorage`, so reloading the page or briefly losing connection keeps you in
the same seat without re-entering anything.

### Label uniqueness

Labels must be unique, case-insensitively. If you pick a name already in use you
get an error before joining (`POST /api/check_label`), and the server rejects
duplicates again at connect time as a safety net. Renames are validated the same
way.

### Position capacity

A show holds a maximum of **16 positions**. Beyond that, new joins are rejected
with a "Show is full" message.

### Switching to the Caller

The join page has a link ("be the caller") that navigates to `/`. It only
succeeds if no Caller is currently connected.

---

## The Position console

A large, touch-friendly screen showing the operator's two cue buttons plus
status information.

### STANDBY button

- When the Caller calls a standby, the button turns **red and flashes**.
- The operator **taps once to acknowledge**. The flashing stops and the button
  shows a steady "acked" state, which tells the Caller the standby was received.
- States cycle: `idle → called (flashing) → acked → idle`.

### GO button

- When the Caller fires GO, the button lights **green**.
- The operator **taps to confirm receipt**, which clears it back to idle.
- Calling GO automatically clears any pending standby on that position.

### Cue information strip

When a showfile is loaded, the header shows this position's current **scene**
and its **cue number** for the active cue (e.g. "Scene 1.2", cue "3"). It
updates automatically as the show advances.

### Health dot

A small dot in the bezel reflects this position's own connection health:

- **Green** — healthy (round-trip latency ≤ 1s)
- **Yellow** — latency > 1s
- **Red** — latency > 3s, or connection lost

### Caller-disconnected warning

If the Caller drops off, a warning banner appears on every Position so operators
know the desk is temporarily offline.

### Leaving / resetting

The **Reset** button on the Position console disconnects this device and returns
it to the join page (clearing the saved label), so the phone can re-join under a
new name or hand off to someone else.

### End-of-show / removal messages

- **Show ended** — when the Caller hits EXIT, every Position shows
  "Show ended — please close this window."
- **Removed** — if the Caller removes a specific position, that device shows
  "You have been removed from the show."

---

## The Caller console

A landscape-oriented grid (best on an iPad). Each connected position is a
column; master controls sit on the right; a bottom bar holds global actions.
A "Please rotate to landscape" overlay appears in portrait orientation.

### Per-position column

Each column shows the position's label, its cue indicator (when a showfile is
loaded), and a "DISCONNECTED" badge if the operator has dropped. It contains
three buttons:

- **STANDBY** — calls a standby on that one position. The button flashes while
  pending and dims once the operator acknowledges, mirroring the operator's
  screen.
- **PRESET** — arms/disarms the position (toggle). Armed positions are the ones
  the master buttons act on. See *Arming*.
- **GO** — fires GO on that one position.

Tapping a position's **header** opens the Edit Position modal (rename / remove).

### Master controls

The MASTER column operates on **all armed positions at once**:

- **STANDBY ARMED** — calls standby on every armed, connected position.
- **RESET ARMED** — clears standby/go and disarms every armed position.
- **GO ARMED** — fires GO on every armed position. If a showfile is loaded,
  this also auto-advances to the next cue (see *Showfiles*).

### Arming (PRESET)

"Armed" marks which positions the master buttons affect. You arm positions
manually with each column's PRESET button, or — when a showfile is loaded — the
system arms the right positions for you on each cue. Firing a standby or GO on a
single position clears its armed flag.

### RESET ALL

In the bottom bar. Clears standby and GO on **every** position (not just armed
ones) back to idle. Useful for a clean slate between acts.

### Lock

The **LOCK** button freezes the whole system:

- **Tap once** to lock. While locked, all cue actions (standby/go/master/reset)
  are ignored server-side, and every Position shows a "SYSTEM LOCKED" overlay so
  operators can't tap during a hold.
- **To unlock, press and hold for 2 seconds** (prevents accidental unlocking).

This is designed for safety during scene changes or breaks.

### EXIT (end the show)

The **EXIT** button (with a confirmation prompt) ends the show: it disconnects
all positions, broadcasts "show ended," and **wipes all saved state**
(deletes `state/snapshot.json`). Use it only when you truly want to clear
everything.

### Settings

The **SETTINGS** button opens a modal with four sections:

1. **Health & Latency** — a live list of every position with its health dot,
   measured latency in ms, and a ⚠️ marker for disconnected positions.
2. **Showfile** — shows the loaded file, a dropdown to **Load** any file in
   `showfiles/`, an **Unload** button, and an **Edit showfile…** button that
   opens the editor in a new tab.
3. **Network** — the server host/address to share with operators.
4. **Security** — the password toggle and field (see *Password protection*).

### Join info / QR code

The bottom bar's **Show join info** button displays a full-screen panel with:

- A **QR code** that encodes the `/join` URL (operators scan it to connect
  instantly).
- The plain-text join URL.
- The password, if one is set (and the QR code embeds it so scanning auto-fills
  it).

Toggle it back off with **Hide join info**.

### Missing-position warnings

When a showfile is loaded and the current cue targets a position that isn't
connected, a warning banner appears on the Caller (e.g.
"Cue 3: Fly not connected"), so you know before firing the cue.

### Renaming and removing positions

Tap any position column header to open **Edit Position**:

- **Rename** — change the label (validated for uniqueness, max 16 chars). The
  operator's screen updates immediately.
- **Remove** — kick the position from the show (with confirmation). That device
  is disconnected and shown the "removed" message.

---

## The button state machine

Both STANDBY and GO move through `idle → called → acked → idle`. The Caller's
column mirrors the Position's state, so when an operator acknowledges, the
Caller's button dims to match.

| Event | STANDBY | GO |
|---|---|---|
| Caller fires standby | idle → called | — |
| Position taps (ack) | called → acked | called → idle |
| Caller fires GO | acked → idle (auto-clear) | idle → called |
| Position taps GO (ack) | — | called → idle |

---

## Showfiles (cue lists)

A showfile is a JSON cue list that lets the Caller step through a show, with the
system auto-arming the right positions and showing each operator their cue
number.

### Loading and running

1. In **Settings → Showfile**, pick a file and tap **Load**. The transport row
   appears at the bottom of the Caller console and positions matching the first
   cue's targets are auto-armed.
2. The transport row shows the current **scene** and the cue's targets
   (e.g. "Scene 1.1 — LX 1, SND 1").
3. Tap **GO ARMED** to fire GO on the armed positions and **auto-advance** to
   the next cue, which re-arms the next set of positions and pushes each operator
   their new cue number.

### Transport controls

The transport row (visible only when a showfile is loaded) has:

- **◀ PREV** — step back one cue (re-arms for that cue).
- **PAUSE / RESUME** — toggle auto-advance. When paused, GO ARMED still fires
  the armed positions but does **not** advance the cue index; manual per-column
  buttons keep working. Use this when you need to hold or repeat a moment.
- **JUMP** — opens a list of every cue (sequence, scene, targets); tap one to
  jump directly to it. The current cue is highlighted.

### Unloading

**Settings → Showfile → Unload** removes the showfile, clears cue indicators on
all positions, and returns to free manual operation.

### Showfile format

JSON files live in the `showfiles/` directory:

```json
{
  "show_name": "Macbeth — Act I",
  "version": 1,
  "cues": [
    {
      "sequence": 1,
      "scene": "1.1",
      "targets": [
        { "position": "LX", "cue_number": "1" },
        { "position": "SND", "cue_number": "1" }
      ],
      "note": "Opening blackout, thunder"
    }
  ]
}
```

- **`show_name`** — display name of the show.
- **`version`** — integer version number.
- **`cues`** — ordered list (sorted by `sequence` on load).
  - **`sequence`** — play order (integer, must be unique within the file).
  - **`scene`** — free-form scene label shown to operators and on the transport.
  - **`targets`** — list of `{ position, cue_number }`. `position` is matched to
    a connected position's label **case-insensitively**; `cue_number` is the
    label that operator sees.
  - **`note`** — optional free-text description.

An example file ships at `showfiles/example.json`.

---

## The showfile editor

A web form for building cue lists without editing JSON by hand. Open it via
**Settings → Edit showfile…** on the Caller, or go to `/editor` directly.

### Creating / loading

- **Load** — pick an existing file from the dropdown to edit it.
- **New** — type a filename and start a blank cue list (`.json` is appended
  automatically if you omit it).

### Editing cues

- Set the **show name** and **version** at the top.
- **Add Cue** appends a row. Each row has columns for **sequence**, **scene**,
  **targets**, and **note**.
- **Targets** are entered as a comma-separated shorthand:
  `LX:1, SND:1, Fly:2` (position`:`cue_number). If you omit the cue number it
  defaults to `1`.
- The **✕** button on a row deletes that cue.

### Saving

**Save** validates the cue list server-side and writes it to `showfiles/`. If
validation fails (missing `show_name`, missing/duplicate `sequence`, missing
`scene`/`targets`, etc.), the errors are shown and nothing is written.

---

## Password protection

Restrict who can join a show.

- **Enable:** Caller → **Settings → Security**, toggle the password on, type a
  password, and **Save**.
- **Joining:** the join page detects that a password is required and shows a
  password field; operators must enter it correctly to join.
- **QR convenience:** when join info is shown with a password set, the QR code
  embeds the password (`/join?pw=…`) so scanning auto-fills it.
- **Disable:** toggle the password off in Settings; the field is cleared.

Verification happens via `POST /api/check_password`.

---

## Health monitoring

The server pings each position every second; the position echoes a `pong` and
the server computes round-trip latency:

- **≤ 1s** → green
- **> 1s** → yellow
- **> 3s** → red
- **3 missed pongs** → marked red (assumed unhealthy)

Operators see their own status as a colored dot in the Position bezel. The Caller
sees every position's dot and latency in **Settings → Health & Latency**, plus a
DISCONNECTED badge on any dropped column.

---

## Reliability features

### Auto-reconnect

Both consoles automatically reconnect if the WebSocket drops, with exponential
backoff (starting at 0.5s, capped at 5s). Because identity is stored in
`localStorage`, reconnecting restores the same seat and state.

### State persistence

The server writes `state/snapshot.json` on every change (debounced at 100ms).
If the server restarts, it restores positions (marked disconnected until they
reconnect), the lock state, the password, the paused flag, and the current cue
index. The **showfile is not stored in the snapshot** — reload it by filename
after a restart. The EXIT button deletes the snapshot entirely.

---

## HTTP / API reference

These endpoints back the UI; you generally won't call them by hand, but they're
useful for integration or debugging.

| Method & path | Purpose |
|---|---|
| `GET /` | Caller console page |
| `GET /join` | Join page |
| `GET /position` | Position console page |
| `GET /editor` | Showfile editor page |
| `GET /api/info` | Server IP, port, caller-connected and password-enabled flags |
| `POST /api/check_password` | Validate a join password |
| `POST /api/check_label` | Check a label is free before joining |
| `GET /api/showfiles` | List available showfile names |
| `GET /api/showfile/{filename}` | Fetch a showfile's JSON |
| `POST /api/showfile/{filename}` | Validate and save a showfile |
| `GET /api/qr?password=…` | PNG QR code for the join URL |
| `WS /ws/caller` | Caller real-time channel |
| `WS /ws/position` | Position real-time channel |

---

## Quick reference: typical show flow

1. Start the server; open `/` on the stage manager's iPad (becomes Caller).
2. (Optional) Build/load a showfile via the editor and Settings.
3. (Optional) Set a join password in Settings → Security.
4. Show **Join info**; operators scan the QR or open `/join`, enter their label,
   and connect.
5. Run the show:
   - **Manual:** use each column's STANDBY / GO, or arm positions with PRESET and
     drive them together with the MASTER buttons.
   - **Showfile:** PRESET arming is automatic; tap **GO ARMED** to fire and
     advance each cue; use PREV / JUMP / PAUSE as needed.
6. **LOCK** during holds; **RESET ALL** between acts.
7. **EXIT** to end the show and clear all state.
