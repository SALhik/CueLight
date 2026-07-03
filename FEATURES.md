# CueLight — Features Guide

CueLight is a browser-based theatre cue light system. One machine runs a Python
server; iPads and phones on the same LAN connect through a browser. One device
acts as the **Caller** (stage manager), and the others are **Positions**
(operators such as LX, Sound, Fly). Show-control gear (QLab, grandMA3,
TheatreMix, or any custom OSC software) can also be fired as **OSC positions**
that appear in the same grid. All real-time signalling happens over WebSockets
and OSC, so cues appear instantly with no page reloads.

This document walks through every feature and how to use it.

---

## Getting started

### Starting the server

**The easy way (no terminal knowledge needed):** double-click or run the
bootstrap script in the project folder — `run.sh` on macOS/Linux
(`./run.sh` from a terminal), `run.bat` on Windows. On first run it creates a
private Python environment and installs everything; after that it just starts
the server and prints the addresses to open on each device. Keep the window
open for the whole show.

**The manual way:**

```bash
pip install -r requirements.txt
python -m server
```

`python -m server` starts the server on port 8000 and prints the join URLs
(LAN IP and `cuelight.local`). The equivalent raw command is
`uvicorn server.main:app --host 0.0.0.0 --port 8000`.

The server listens on all interfaces on port 8000. Find the host machine's LAN
IP (Settings shows it, or `/api/info` returns it) and share
`http://<ip>:8000` with the other devices.

The server also advertises itself on the LAN via mDNS/Bonjour as
**`cuelight.local`**, so devices that support it (iPhones/iPads, Macs,
Windows 10+, newer Android) can simply type `http://cuelight.local:8000`
instead of an IP. This is best-effort: if the name is taken or the network
blocks multicast, everything falls back to the IP and QR flow. The Caller's
Settings → Network and the join-info screen both show the friendly URL when
it's active.

### The roles / pages

| URL | Page | Who uses it |
|---|---|---|
| `/` | Caller console | Stage manager (one device only) |
| `/join` → `/position` | Position console | Each operator |
| `/observer` | Observer console (read-only) | Director, production manager, backup caller |
| `/editor` | Showfile & patch editor | Whoever prepares the cue list or venue patch |

The **first** device to open `/` claims the Caller role. If a second device
opens `/` while a Caller is already connected, it is automatically redirected
to the join page to become a Position instead. The Caller's own device can
always reclaim its seat: reloading the page (or reconnecting after the iPad
slept) takes over from the stale connection without the Positions ever seeing
a "caller disconnected" warning.

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

Labels must be unique, case-insensitively — across both human and OSC positions.
If you pick a name already in use (including one claimed by an OSC device in the
loaded patch) you get an error before joining (`POST /api/check_label`), and the
server rejects duplicates again at connect time as a safety net. Renames are
validated the same way.

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
and its **cue number** for the active cue (e.g. "Scene 1.2", cue "3"). If the
current cue has a **note**, it appears in italic below the header strip. Notes
update automatically as the show advances and hide when the cue has no note.

### Label color

The position's label in the bottom bezel is displayed as a colored pill,
matching the color assigned by the Caller. This helps operators confirm they are
looking at the right device and matches what the Caller sees on their grid.

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

### Flash check (roll call)

Before the house opens, the Caller can run a **flash check**: every connected
Position's screen blinks bright yellow with "FLASH CHECK — TAP TO CONFIRM."
Tapping the overlay confirms the operator is present and awake; the Caller
watches the confirmations tick in live (see *Settings → Health & Latency*).
An incoming standby or GO dismisses the overlay automatically, so a real cue
can never be blocked by it.

### Sound & vibration alerts (opt-in)

An operator watching the stage can miss a silent light change. The **ALERT**
button in the bottom bezel turns on audible/tactile alerts for incoming cues:

- **Standby** — two short high beeps (and a double vibration pulse on
  Android).
- **GO** — one long lower beep (and a long vibration).

The patterns are distinct so you can tell them apart without looking. Off by
default, per device, and the choice survives page reloads. Turning it on plays
a small confirmation blip — that first tap also unlocks the phone's audio, so
alerts work from then on. (iPhones can't vibrate from the browser; the beep is
the alert there.)

### Raising a PROBLEM

Operators can signal the caller that something is wrong — without leaving
their post or typing in the dark:

1. Tap **PROBLEM** in the bottom bezel. A panel opens *below* the cue buttons
   (they stay visible and tappable — an incoming standby or GO also closes the
   panel automatically, so a cue can never be blocked by it).
2. Tap one of the big preset messages — **NOT READY**, **PROP ISSUE**,
   **NEED SM** — or type a short message (60 characters max) and tap
   **RAISE**. A preset is one tap; typing is optional.
3. The PROBLEM button turns orange while the flag is up. Re-raising replaces
   the message; **CLEAR PROBLEM** (in the panel) withdraws it.

On the Caller grid the position's column gets a **solid orange ⚠ PROBLEM
badge and outline** — deliberately steady, not flashing, so it never disturbs
the calling rhythm mid-sequence. The caller taps the badge to read the message
and can clear it from there. The flag survives reconnects and even a server
restart, and every raise/clear is recorded in the show log with its message.
Observers see the flag (and message) too.

### Screen dimming (running mode)

Backstage must be dark, and a phone at normal brightness glows like a lantern.
The **DIM** button in the bottom bezel cycles through three modes:

- **off** — normal brightness.
- **DIM: ON** — a dark overlay drops the whole screen to a fraction of its
  light output. Cue colors still read clearly in a dark wing.
- **DIM: RED** — the same, with a red cast that preserves night vision (like
  backstage running lights).

The overlay is purely visual — every tap passes straight through it, so acking
a standby works exactly the same while dimmed. The chosen mode survives page
reloads. Use it only if needed; it's off by default.

### Screen keep-awake

Phones dim and lock themselves — fatal if it happens right before a cue. After
the operator's first tap on the page, the console plays an invisible muted
looping video, which keeps the screen awake on iOS Safari and Android Chrome
(the proper Wake Lock API needs HTTPS, which a LAN HTTP app can't use). No
setup needed; one tap anywhere arms it. The Caller and Observer pages do the
same.

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

Each column shows the position's label inside a **colored pill** for quick
identification, its cue indicator (when a showfile is loaded), and a
"DISCONNECTED" badge if the operator has dropped. It contains three buttons:

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

### EXIT (end the show) — and resuming

The **EXIT** button (with a confirmation prompt) ends the show: it disconnects
all positions and broadcasts "show ended." The saved state is **archived, not
destroyed** — `state/snapshot.json` is renamed to `state/snapshot.bak` (and the
show log to `showlog.bak`), so a mis-tapped EXIT in the middle of tech is
recoverable:

- The "Show ended" screen offers a **RESUME SHOW** button when a backup exists.
- In a new show, **Settings → Previous Show** shows what's in the backup
  (showfile name, position count) and a **Resume…** button.

Resuming restores the positions, lock, password, cue index, showfile, OSC patch,
and show log. Operators just reload their phones (or wait for auto-reconnect)
and land back in their seats. Starting a *new* show after an EXIT overwrites the
backup at the next EXIT.

### START SHOW and the show timer

At the top of show, press **START SHOW** in the bottom bar (a confirmation
guards against mis-taps). This records the wall-clock start time, notes it in
the show log, and shows every connected operator a brief "SHOW STARTED"
banner that dismisses itself after a few seconds — it floats over the header
strip, never the cue buttons. Pressing START SHOW again simply re-records the
time (with another confirmation and a log entry), for a false start or a
per-act reset. The start time survives a mid-show server restart; EXIT clears
it.

Next to the button, a **compact timer** shows — in quiet grey text that never
competes with cue state — any combination of:

- time since show start (`SHOW 1:23:45`),
- time since the last GO (`GO +02:10`),
- the current clock time.

Pick which in **Settings → Show Timer** (first two on by default; the choice
is per device and persists).

### Problem markers

When an operator raises a **PROBLEM** (see the Position console section), the
column shows a steady orange **⚠ PROBLEM** badge and outline. Tap the badge to
read the operator's message and clear the flag. The marker never flashes, so
it won't be confused with a called standby. If the position also disconnects,
the DISCONNECTED badge takes over but the orange outline remains.

### Settings

The **SETTINGS** button opens a modal with these sections:

1. **Health & Latency** — a live list of every position (human and OSC) with its
   health dot, measured latency (human) or trust tier (OSC), and a ⚠️ marker for
   disconnected human positions. The **Flash all positions** button runs the
   roll call: each connected Position blinks until its operator taps to confirm,
   and the list shows "⏳ waiting" / "✔ here" per position, updating live.
   **Clear check** resets the markers.
2. **Showfile** — shows the loaded file, a dropdown to **Load** any file in
   `showfiles/`, an **Unload** button, an **Edit showfile…** button that
   opens the editor in a new tab, and the **Auto-standby next cue after GO**
   toggle (see *Showfiles*).
3. **OSC Patch** — shows the loaded patch, a dropdown to **Load** any patch in
   `patches/`, an **Unload** button, and an **Edit patches…** button that opens
   the editor's Patches tab. See *OSC outbound*.
4. **Network** — the server host/address to share with operators, including the
   `cuelight.local` mDNS name when it's active.
5. **Show Timer** — checkboxes choosing what the bottom-bar timer displays:
   time since show start, time since last GO, clock time (see *START SHOW and
   the show timer*). Per-device, persisted.
6. **Show Log** — download the show's event log as CSV, view the raw JSON, or
   generate the **post-show report** (HTML page or CSV — see *Show log &
   post-show report*).
7. **Previous Show** — appears only when an EXIT backup exists; resumes it.
8. **Security** — the password toggle and field (see *Password protection*).

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

### Position colors

Each position is auto-assigned a color from an 8-color palette when it joins
(or when an OSC patch is loaded). The color appears as a **pill** behind the
label — on both the Caller grid header and the Position's bottom bezel — so the
Caller can quickly identify positions at a glance, even with many columns. The
palette avoids red, green, and yellow to not clash with the standby/go/preset
button states.

To **change a position's color**, tap its column header to open the Edit
Position modal and pick a swatch from the color row. The change applies
immediately and is visible on both the Caller and the operator's screen. Colors
persist across page reloads.

### Renaming and removing positions

Tap any position column header to open **Edit Position**:

- **Color** — a row of swatches to change the position's identity color (see
  *Position colors* above).
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
2. The transport row shows the current **scene**, the cue's targets
   (e.g. "Scene 1.1 — LX 1, SND 1"), and the cue's **note** (if any) in
   italic below.
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

### Auto-standby (optional)

**Settings → Showfile → Auto-standby next cue after GO** (off by default).
When enabled, every GO ARMED that advances the cue also calls standby on the
*next* cue's targets immediately — matching the warn → standby → go rhythm
without the Caller having to tap STANDBY ARMED between every cue. Armed OSC
positions get their readiness probe instead. Jumping or stepping back through
cues never auto-calls standby; only a fired GO does. The setting persists
across server restarts.

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
  - **`note`** — optional free-text note, displayed on the Caller transport
    and on each targeted Position's screen.

An example file ships at `showfiles/example.json`.

---

## The editor

A web form for building cue lists and OSC patches without editing JSON by hand.
Open it via **Settings → Edit showfile…** or **Settings → Edit patches…** on
the Caller, or go to `/editor` directly. The editor has two tabs: **Showfiles**
and **OSC Patches**.

### Showfiles tab

#### Creating / loading

- **Load** — pick an existing file from the dropdown to edit it.
- **New** — type a filename and start a blank cue list (`.json` is appended
  automatically if you omit it).

#### Editing cues

- Set the **show name** and **version** at the top.
- **Add Cue** appends a row. Each row has columns for **sequence**, **scene**,
  **targets**, and **note**.
- **Targets** are entered as a comma-separated shorthand:
  `LX:1, SND:1, Fly:2` (position`:`cue_number). If you omit the cue number it
  defaults to `1`.
- The **✕** button on a row deletes that cue.

#### Saving

**Save** validates the cue list server-side and writes it to `showfiles/`. If
validation fails (missing `show_name`, missing/duplicate `sequence`, missing
`scene`/`targets`, etc.), the errors are shown and nothing is written.

#### CSV import / export

Cue sheets usually live in spreadsheets, so the Showfiles tab can exchange the
cue list with CSV:

- **Import CSV…** — pick a `.csv` file; its rows replace the editor's cue list
  (with a confirmation if you already have cues). Parse errors are reported by
  row number and nothing is replaced.
- **Export CSV** — downloads the current cue list as a `.csv` named after the
  loaded file.

The format is one row per cue with a required header row:

```csv
sequence,scene,targets,note
1,1.1,LX:1;SND:1,"Opening blackout, thunder"
2,1.2,Fly 1:12a,Witches enter
```

- Columns may be in any order; unknown columns are ignored; `note` is optional.
- **`targets`** holds `POSITION:CUE` pairs separated by `;`. The split is on the
  *last* colon, so labels may contain spaces (`Fly 1:12a` → position "Fly 1",
  cue "12a").
- Excel's UTF-8 BOM is handled; quoted cells with commas work as usual.

A sample ships at `showfiles/example.csv` (the CSV twin of `example.json`) —
open it in a spreadsheet as a starting template. Conversion happens server-side
(`POST /api/csv/import` and `POST /api/csv/export`), so the dialect is identical
in both directions.

### OSC Patches tab

#### Creating / loading

Same workflow as showfiles — **Load** an existing patch or **New** to start
fresh.

#### Editing devices

- Set the **patch name** at the top.
- **Add device** appends a row. Each row has columns for **name**, **preset**,
  **IP**, **port**, **protocol**, **GO template**, **GO args**, **ping
  template**, and **expect reply**.
- Selecting a **preset** (QLab 5, grandMA3, TheatreMix) prefills the fields
  with sensible defaults for that console. Every field stays editable.
- The **Test** button probes the device and shows its trust tier (e.g.
  `osc_reply`, `tcp_port`, or `unverified`).
- The **✕** button removes a device row.

#### Saving

**Save** validates the patch server-side (checks for missing names/IPs,
duplicate names, invalid ports) and writes it to `patches/`.

---

## OSC outbound

OSC (Open Sound Control) lets CueLight fire show-control gear — lighting desks,
sound playback, band monitors — as a natural part of calling cues. An OSC target
appears as a **virtual position** in the Caller grid: same STANDBY / PRESET / GO
buttons, same arming and showfile auto-advance, but instead of a human tapping a
phone, the server sends an OSC message to the target device.

### OSC patches

An OSC patch is a JSON file in the `patches/` directory that describes the
devices at a venue — their IPs, ports, protocols, and OSC addresses. Patches are
**decoupled from showfiles** by design: cues travel with the production, but
IPs and ports are venue-specific.

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
    }
  ]
}
```

Each device has:

- **`name`** — the position label and showfile cue target (case-insensitive
  match, same as human positions). Must be unique across all positions.
- **`preset`** — `qlab5`, `grandma3`, `theatremix`, or `custom`. Presets
  prefill the fields below in the editor; every field stays editable.
- **`ip`** / **`port`** / **`protocol`** (`udp` or `tcp`) — network target.
- **`go_template`** — the OSC address sent on GO. Use `{cue}` as a placeholder
  for the showfile cue number (e.g. `/cue/{cue}/start` → `/cue/12/start`).
  No placeholder means the address is sent verbatim.
- **`go_args`** — optional static OSC arguments appended to every fire.
- **`ping_template`** — an OSC address used for the readiness probe (STANDBY).
  Empty means the device is probed by TCP-port check or marked unverified.
- **`expect_reply`** — whether to wait for a reply to confirm fire/probe.

An example file ships at `patches/mainstage.json`.

### Loading a patch

In **Settings → OSC Patch**, pick a file and tap **Load**. Each device in the
patch becomes a column in the Caller grid, visually distinguished by a blue
accent border and an **OSC** badge in the header. The columns behave identically
to human positions for arming, Master GO, and showfile cue targeting.

Unload a patch via Settings or by calling EXIT (which wipes all state).

### How OSC columns work

| Button | Human position | OSC position |
|---|---|---|
| **STANDBY** | Sends "standby called" to the operator | Runs a **readiness probe** (see below) |
| **PRESET** | Arms/disarms (same for both) | Arms/disarms (same) |
| **GO** | Sends "go called" to the operator | **Fires the OSC message** to the target device |

GO on an OSC column **always dispatches** — it is never blocked by probe state.
If a device is unreachable, the fire still goes out and the result reports
honestly.

### Readiness probes (STANDBY on OSC)

Tapping STANDBY on an OSC column runs a tiered probe to check if the device is
reachable:

| Tier | Condition | Result on success | Result on failure |
|---|---|---|---|
| **OSC reply** | `ping_template` set + `expect_reply` | CONFIRMED / "app confirmed responsive" | FAILED / "app confirmed responsive" |
| **TCP port** | Fallback: TCP connect to ip:port | CONFIRMED / "app's port is listening" | FAILED / "app's port is listening" |
| **Unverified** | UDP-only, no ping template | UNVERIFIED / "not confirmed — firing blind" | — |

The probe result shows on the STANDBY button:

- **Solid bright red** — CONFIRMED (reachable).
- **Solid bright red + ∅ glyph** — UNVERIFIED (no ping configured; assumed
  ready but not confirmed).
- **Flashing bright red** — PROBING (in progress) or FAILED (unreachable —
  treat like a human who hasn't acked).

STANDBY always re-probes, even if already CONFIRMED, so you can demand a fresh
check right before firing GO.

### GO fire and results

When GO fires on an OSC column:

1. The `go_template` is resolved (replacing `{cue}` with the showfile cue
   number, if present).
2. The message is sent to the device over UDP or TCP.
3. If `expect_reply` is true, the server waits up to 400ms for a reply.
4. The result is sent to the Caller as an `osc_result` message.

The GO button shows the result for **2 seconds**:

- **SENT** (green hold) — the fire was dispatched and confirmed (or open-loop,
  meaning `expect_reply` was false — the fire still happened).
- **NO REPLY** (flashing red) — the fire was dispatched but no reply was received
  within 400ms. This means "couldn't confirm it landed," not "didn't fire."

OSC network I/O runs in the background: fires and probes for a cue happen
concurrently, and a slow or unreachable device never delays the cue lights on
human positions or blocks the Caller's next action.

### Background heartbeat

Devices that have been probed at least once with a confirmable tier (OSC reply or
TCP port) are re-probed every ~5 seconds to keep the health dot accurate. This
catches a mid-scene cable pull or crashed console. UDP-only/unverified devices
are not heartbeated.

### Editing patches

The editor at `/editor` has two tabs: **Showfiles** and **OSC Patches**. The
Patches tab works the same way as the Showfiles tab — select or create a file,
add device rows, and save.

Each device row has a **Preset** selector that prefills fields for common
consoles (QLab 5, grandMA3, TheatreMix). A **Test** button per row runs the
probe and shows the resulting trust tier.

---

## The Observer console (read-only)

`/observer` is a read-only mirror of the Caller's grid, meant for a director,
production manager, or a **standby caller device**. Like the Caller console it
is designed for an iPad in landscape (a rotate prompt appears in portrait).

- Reach it from the join page via **Watch as Observer (read-only)**. If the show
  is password-protected, the same password is required.
- Observers see the live position columns (standby/acked/GO states, arming,
  colors, OSC probe states), the transport row, missing-position warnings, and a
  LOCKED tag — but nothing on the page sends commands. Any number of observers
  can watch at once. The join password is never sent to observers.
- The status bar shows whether the Caller is connected. If the Caller drops, the
  bar flashes **CALLER DISCONNECTED** and a **TAKE OVER AS CALLER** button
  appears. Taking over is always a deliberate tap (never automatic): it opens
  the Caller console on this device, which claims the now-vacant caller seat
  with the full show state intact.

---

## Show log & post-show report

The server keeps a timestamped log of everything that happens in the show:
standbys and acks (with the operator and the cue they happened on), GOs, master
actions, cue advances and jumps, OSC fire results, lock/unlock, pauses,
positions joining/leaving, showfile/patch loads, flash-check confirmations,
problems raised/cleared (with their messages), and START SHOW presses.
Auto-called standbys are marked `auto` so a report distinguishes them from
manual calls.

- **Download:** Caller → **Settings → Show Log** — CSV (columns
  `time,event,position,cue,detail`) for spreadsheets, or raw JSON at
  `GET /api/showlog`.
- **Persistence:** entries append to `state/showlog.jsonl` as they happen, so
  the log survives a server restart mid-show.
- **Lifecycle:** EXIT archives the log next to the state backup; resuming the
  show brings it back, so a resumed show's report is complete. A new show starts
  a fresh log.

It doubles as a debugging trail: if a cue was missed, the log shows exactly
when the standby went out and when (or whether) it was acknowledged.

### Post-show report

After (or during) a show, **Settings → Show Log → Show report** turns the log
into a readable report — computed on demand from the log, with nothing extra
stored on the server:

- **Standby response times** per position (min / average / max) and per cue —
  how quickly each operator acknowledged their standbys.
- **Standbys never acknowledged** — standbys that were still un-acked when the
  GO fired (or when the show ended).
- **Time between GOs** — the pace of the show, cue by cue.
- **Auto vs manual standby counts**, **joins/disconnects per position**, and
  every **problem raised** with its message and how it was cleared.

The report is a self-contained HTML page (`GET /api/showreport`) you can save
or print, and **Report CSV** (`?format=csv`) downloads the same numbers as a
flat spreadsheet-friendly CSV.

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
reconnect), the lock state, the password, the paused flag, the auto-standby
setting, and the current cue index. The **showfile and OSC patch are reloaded
automatically** on startup by their stored filenames, and the show resumes at
the cue it was on — a mid-show server restart doesn't lose your place. The EXIT
button archives the snapshot to `state/snapshot.bak` instead of deleting it, so
an accidental EXIT can be undone with **Resume** (see *EXIT — and resuming*).

---

## HTTP / API reference

These endpoints back the UI; you generally won't call them by hand, but they're
useful for integration or debugging.

| Method & path | Purpose |
|---|---|
| `GET /` | Caller console page |
| `GET /join` | Join page |
| `GET /position` | Position console page |
| `GET /observer` | Observer console page (read-only) |
| `GET /editor` | Showfile & patch editor page |
| `GET /api/info` | Server IP, port, mDNS hostname, caller-connected and password-enabled flags |
| `POST /api/check_password` | Validate a join password |
| `POST /api/check_label` | Check a label is free before joining |
| `GET /api/showfiles` | List available showfile names |
| `GET /api/showfile/{filename}` | Fetch a showfile's JSON |
| `POST /api/showfile/{filename}` | Validate and save a showfile |
| `POST /api/csv/import` | Convert CSV text to showfile cues (or row errors) |
| `POST /api/csv/export` | Convert showfile JSON to downloadable CSV |
| `GET /api/patches` | List available OSC patch names |
| `GET /api/patch/{filename}` | Fetch a patch's JSON |
| `POST /api/patch/{filename}` | Validate and save a patch (or probe-test with `_probe_test`) |
| `GET /api/showlog` | Show event log as JSON (`?format=csv` for CSV download) |
| `GET /api/showreport` | Post-show report as a self-contained HTML page (`?format=csv` for CSV) |
| `GET /api/backup_info` | Whether an EXIT backup exists (plus showfile/position count) |
| `POST /api/resume_show` | Restore the EXIT backup into the running server |
| `GET /api/qr?password=…` | PNG QR code for the join URL |
| `WS /ws/caller` | Caller real-time channel |
| `WS /ws/position` | Position real-time channel |
| `WS /ws/observer` | Observer real-time channel (read-only mirror) |

---

## Quick reference: typical show flow

1. Start the server; open `/` on the stage manager's iPad (becomes Caller).
2. (Optional) Build/load a showfile via the editor and Settings.
3. (Optional) Build/load an OSC patch via the editor and Settings → OSC Patch.
   OSC devices appear as columns in the grid alongside human positions.
4. (Optional) Set a join password in Settings → Security.
5. Show **Join info**; operators scan the QR or open `/join`, enter their label,
   and connect. A director or backup caller can pick **Watch as Observer** on
   the join page instead.
6. Before the house opens, run a **flash check** (Settings → Health & Latency →
   Flash all positions) and watch every operator confirm. Operators who want
   audible cues turn on **ALERT** in their bezel.
7. At the top of show, press **START SHOW** — the clock starts and every
   operator sees a brief notice.
8. Run the show:
   - **Manual:** use each column's STANDBY / GO, or arm positions with PRESET and
     drive them together with the MASTER buttons.
   - **Showfile:** PRESET arming is automatic (including OSC positions); tap
     **GO ARMED** to fire human and OSC positions together and advance each cue;
     use PREV / JUMP / PAUSE as needed.
   - **OSC:** STANDBY on an OSC column probes the device; GO fires the OSC
     message. Results (SENT / NO REPLY) show on the button for 2 seconds.
9. **LOCK** during holds; **RESET ALL** between acts. Operators flag trouble
   with **PROBLEM**; the caller reads and clears the orange badges.
10. **EXIT** to end the show. Afterwards, download the show log or generate
    the **post-show report** from Settings, or **RESUME SHOW** if the EXIT
    was a mistake.
