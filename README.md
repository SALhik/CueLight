# CueLight

A browser-based theatre cue light system. One machine runs the server; other devices on the same LAN connect as the Caller (stage manager's iPad) or Positions (operator phones).

For a full walkthrough of every feature and how to use it, see [FEATURES.md](FEATURES.md).

## Requirements

Python 3.10 or newer. Python 3.12 is recommended — the project is developed, tested, and built on 3.12.

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Then open `http://<your-ip>:8000` on the Caller device. The first connection becomes the Caller. Other devices connect to the same URL and are offered the Position (join) page.

## How it works

- **Caller** sees a grid of connected Positions with STANDBY / PRESET / GO buttons, plus master controls that operate on all armed positions.
- **Positions** see large STANDBY and GO buttons. When the Caller calls STANDBY, the button flashes red. The operator taps to acknowledge. When the Caller calls GO, the button lights green; the operator taps to confirm receipt.
- **OSC devices** (QLab, grandMA3, TheatreMix, or any custom OSC software) can be added as virtual positions via an OSC patch. They appear in the same grid — STANDBY probes the device, GO fires an OSC message.
- A **showfile** can be loaded to auto-advance through cues and auto-arm target positions (including OSC positions).

## Showfile format

JSON files in `showfiles/`. Example:

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

- `sequence`: play order (integer)
- `scene`: free-form scene label
- `targets`: list of `{ position, cue_number }` — matched by display label (case-insensitive)
- `note`: optional description

An example showfile is included at `showfiles/example.json`.

## OSC patch format

JSON files in `patches/` define the OSC devices at a venue. Patches are decoupled from showfiles — cues travel with the production, IPs are venue-specific.

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
    }
  ]
}
```

- `name`: position label and showfile cue target (must be unique across all positions)
- `preset`: `qlab5`, `grandma3`, `theatremix`, or `custom` (prefills editor fields)
- `go_template`: OSC address for GO; `{cue}` is replaced with the showfile cue number
- `ping_template`: OSC address for the readiness probe (empty = TCP-port check or unverified)
- `expect_reply`: whether to wait for a reply to confirm fire/probe

An example patch is included at `patches/mainstage.json`.

## Editor

Access Settings > Edit showfile or Edit patches, or navigate to `/editor` directly. The editor has tabs for showfiles and OSC patches.
