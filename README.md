# CueLight

A browser-based theatre cue light system. One machine runs the server; other devices on the same LAN connect as the Caller (stage manager's iPad) or Positions (operator phones).

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
- A **showfile** can be loaded to auto-advance through cues and auto-arm target positions.

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

## Showfile editor

Access Settings > Edit showfile, or navigate to `/editor` directly.
