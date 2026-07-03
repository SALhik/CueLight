from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .files import require_safe_filename
from .models import OscDevice, OscPatch

PATCHES_DIR = Path(__file__).resolve().parent.parent / "patches"


def list_patches() -> list[str]:
    PATCHES_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(f.name for f in PATCHES_DIR.glob("*.json"))


def load_patch(filename: str) -> OscPatch:
    require_safe_filename(filename)
    path = PATCHES_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Patch not found: {filename}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in patch file {filename}: {e}") from e
    return _parse_patch(data, filename)


def validate_patch(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "name" not in data:
        errors.append("Missing 'name'")
    if "devices" not in data or not isinstance(data.get("devices"), list):
        errors.append("Missing or invalid 'devices' array")
        return errors
    names_seen: set[str] = set()
    for i, dev in enumerate(data["devices"]):
        if "name" not in dev or not dev["name"].strip():
            errors.append(f"Device {i}: missing 'name'")
        elif dev["name"].strip().lower() in names_seen:
            errors.append(f"Device {i}: duplicate name '{dev['name']}'")
        else:
            names_seen.add(dev["name"].strip().lower())
        if "ip" not in dev or not dev["ip"].strip():
            errors.append(f"Device {i}: missing 'ip'")
        if "port" not in dev:
            errors.append(f"Device {i}: missing 'port'")
        elif not isinstance(dev["port"], int) or dev["port"] < 1 or dev["port"] > 65535:
            errors.append(f"Device {i}: invalid port")
        if "go_args" in dev and not isinstance(dev.get("go_args"), list):
            errors.append(f"Device {i}: 'go_args' must be a list")
        if "expect_reply" in dev and not isinstance(dev.get("expect_reply"), bool):
            errors.append(f"Device {i}: 'expect_reply' must be a boolean")
        proto = dev.get("protocol", "udp")
        if proto not in ("udp", "tcp"):
            errors.append(f"Device {i}: protocol must be 'udp' or 'tcp'")
    return errors


def save_patch(filename: str, data: dict[str, Any]) -> None:
    require_safe_filename(filename)
    errors = validate_patch(data)
    if errors:
        raise ValueError(f"Invalid patch data: {'; '.join(errors)}")
    PATCHES_DIR.mkdir(parents=True, exist_ok=True)
    path = PATCHES_DIR / filename
    path.write_text(json.dumps(data, indent=2))


def _parse_patch(data: dict[str, Any], filename: str) -> OscPatch:
    devices = []
    for d in data.get("devices", []):
        devices.append(OscDevice(
            name=d["name"],
            ip=d["ip"],
            port=d["port"],
            protocol=d.get("protocol", "udp"),
            go_template=d.get("go_template", ""),
            go_args=d.get("go_args", []),
            ping_template=d.get("ping_template", ""),
            expect_reply=d.get("expect_reply", True),
            preset=d.get("preset", "custom"),
        ))
    return OscPatch(
        name=data.get("name", ""),
        devices=devices,
        filename=filename,
    )
