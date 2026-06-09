from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket

from . import osc as osc_mod
from .models import (
    AppState, ButtonState, HealthStatus, OscDevice, OscFireResult,
    OscPatch, OscProbeState, Position, PositionType,
)
from .persistence import save_state


class StateManager:
    MAX_POSITIONS = 16

    def __init__(self, initial: AppState | None = None) -> None:
        self.state = initial or AppState()
        self.caller_ws: WebSocket | None = None
        self.position_ws: dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()
        self.osc_devices: dict[str, OscDevice] = {}
        self._osc_heartbeat_task: asyncio.Task | None = None

    def _persist(self) -> None:
        save_state(self.state)

    # --- Caller management ---

    async def register_caller(self, ws: WebSocket, client_id: str) -> bool:
        async with self._lock:
            if self.state.caller_connected:
                return False
            self.state.caller_connected = True
            self.state.caller_client_id = client_id
            self.caller_ws = ws
            self._persist()
            return True

    async def unregister_caller(self) -> None:
        async with self._lock:
            self.state.caller_connected = False
            self.caller_ws = None
            self._persist()
            await self._broadcast_positions({"type": "caller_disconnected"})

    # --- Position management ---

    async def register_position(self, ws: WebSocket, client_id: str, label: str) -> bool | str:
        async with self._lock:
            if client_id not in self.state.positions:
                for p in self.state.positions.values():
                    if p.label.lower() == label.lower():
                        return "duplicate"
            if client_id in self.state.positions:
                pos = self.state.positions[client_id]
                pos.connected = True
                pos.label = label
                pos.health = HealthStatus.GREEN
            else:
                if len(self.state.positions) >= self.MAX_POSITIONS:
                    return False
                self.state.positions[client_id] = Position(
                    client_id=client_id, label=label
                )
            self.position_ws[client_id] = ws
            self._update_cue_indicators()
            self._persist()
            await self._notify_caller_full_state()
            return True

    async def unregister_position(self, client_id: str) -> None:
        async with self._lock:
            if client_id in self.state.positions:
                self.state.positions[client_id].connected = False
                self.state.positions[client_id].health = HealthStatus.RED
            self.position_ws.pop(client_id, None)
            self._persist()
            await self._notify_caller_full_state()

    async def remove_position(self, client_id: str) -> None:
        async with self._lock:
            self.state.positions.pop(client_id, None)
            ws = self.position_ws.pop(client_id, None)
            if ws:
                try:
                    await ws.send_json({"type": "removed"})
                    await ws.close()
                except Exception:
                    pass
            self._persist()
            await self._notify_caller_full_state()

    # --- Button actions ---

    async def call_standby(self, client_id: str) -> None:
        async with self._lock:
            pos = self.state.positions.get(client_id)
            if not pos or self.state.locked:
                return
            self._clear_transient_osc_results()
            if pos.type == PositionType.OSC:
                device = self.osc_devices.get(client_id)
                if device:
                    pos.osc_probe = OscProbeState.PROBING
                    probe_state, trust = await osc_mod.probe(device)
                    pos.osc_probe = probe_state
                    pos.osc_trust = trust
                self._persist()
                await self._notify_caller_full_state()
            else:
                pos.standby = ButtonState.CALLED
                pos.armed = False
                self._persist()
                await self._send_position(client_id, {
                    "type": "standby_called",
                })
                await self._notify_caller_full_state()

    async def ack_standby(self, client_id: str) -> None:
        async with self._lock:
            pos = self.state.positions.get(client_id)
            if not pos or pos.standby != ButtonState.CALLED:
                return
            pos.standby = ButtonState.ACKED
            self._persist()
            await self._notify_caller_full_state()

    async def call_go(self, client_id: str) -> None:
        async with self._lock:
            pos = self.state.positions.get(client_id)
            if not pos or self.state.locked:
                return
            self._clear_transient_osc_results()
            if pos.type == PositionType.OSC:
                pos.standby = ButtonState.IDLE
                pos.armed = False
                device = self.osc_devices.get(client_id)
                if device:
                    cue_number = self._get_cue_number_for_position(pos.label)
                    result = await osc_mod.fire(device, cue_number)
                    pos.osc_fire_result = result
                    await self._send_osc_result(client_id, result.value)
                self._persist()
                await self._notify_caller_full_state()
            else:
                pos.standby = ButtonState.IDLE
                pos.go = ButtonState.CALLED
                pos.armed = False
                self._persist()
                await self._send_position(client_id, {
                    "type": "go_called",
                })
                await self._notify_caller_full_state()

    async def ack_go(self, client_id: str) -> None:
        async with self._lock:
            pos = self.state.positions.get(client_id)
            if not pos or pos.go != ButtonState.CALLED:
                return
            pos.go = ButtonState.IDLE
            self._persist()
            await self._notify_caller_full_state()

    # --- Master actions ---

    async def standby_armed(self) -> None:
        async with self._lock:
            if self.state.locked:
                return
            self._clear_transient_osc_results()
            for pos in self.state.positions.values():
                if pos.armed and pos.connected:
                    if pos.type == PositionType.OSC:
                        device = self.osc_devices.get(pos.client_id)
                        if device:
                            pos.osc_probe = OscProbeState.PROBING
                            probe_state, trust = await osc_mod.probe(device)
                            pos.osc_probe = probe_state
                            pos.osc_trust = trust
                    else:
                        pos.standby = ButtonState.CALLED
                        await self._send_position(pos.client_id, {"type": "standby_called"})
            self._persist()
            await self._notify_caller_full_state()

    async def go_armed(self) -> None:
        async with self._lock:
            if self.state.locked:
                return
            self._clear_transient_osc_results()
            for pos in self.state.positions.values():
                if pos.armed and pos.connected:
                    if pos.type == PositionType.OSC:
                        pos.standby = ButtonState.IDLE
                        pos.armed = False
                        device = self.osc_devices.get(pos.client_id)
                        if device:
                            cue_number = self._get_cue_number_for_position(pos.label)
                            result = await osc_mod.fire(device, cue_number)
                            pos.osc_fire_result = result
                            await self._send_osc_result(pos.client_id, result.value)
                    else:
                        pos.standby = ButtonState.IDLE
                        pos.go = ButtonState.CALLED
                        pos.armed = False
                        await self._send_position(pos.client_id, {"type": "go_called"})
            self._advance_cue()
            self._persist()
            await self._broadcast_positions_cue_info()
            await self._notify_caller_full_state()

    async def reset_armed(self) -> None:
        async with self._lock:
            if self.state.locked:
                return
            for pos in self.state.positions.values():
                if pos.armed:
                    pos.standby = ButtonState.IDLE
                    pos.go = ButtonState.IDLE
                    pos.armed = False
                    await self._send_position(pos.client_id, {
                        "type": "state_reset",
                    })
            self._persist()
            await self._notify_caller_full_state()

    async def reset_all(self) -> None:
        async with self._lock:
            if self.state.locked:
                return
            for pos in self.state.positions.values():
                pos.standby = ButtonState.IDLE
                pos.go = ButtonState.IDLE
                await self._send_position(pos.client_id, {
                    "type": "state_reset",
                })
            self._persist()
            await self._notify_caller_full_state()

    # --- Arm / disarm ---

    async def toggle_arm(self, client_id: str) -> None:
        async with self._lock:
            pos = self.state.positions.get(client_id)
            if not pos:
                return
            pos.armed = not pos.armed
            self._persist()
            await self._notify_caller_full_state()

    # --- Label ---

    async def rename_position(self, client_id: str, new_label: str) -> bool:
        async with self._lock:
            pos = self.state.positions.get(client_id)
            if not pos:
                return False
            for cid, p in self.state.positions.items():
                if cid != client_id and p.label.lower() == new_label.lower():
                    return False
            pos.label = new_label
            self._update_cue_indicators()
            self._persist()
            await self._send_position(client_id, {
                "type": "label_changed",
                "label": new_label,
            })
            await self._notify_caller_full_state()
            return True

    # --- Lock ---

    async def set_lock(self, locked: bool) -> None:
        async with self._lock:
            self.state.locked = locked
            self._persist()
            await self._broadcast_positions({"type": "lock_changed", "locked": locked})
            await self._notify_caller_full_state()

    # --- Password ---

    async def set_password(self, enabled: bool, password: str = "") -> None:
        async with self._lock:
            self.state.password_enabled = enabled
            self.state.password = password if enabled else ""
            self._persist()
            await self._notify_caller_full_state()

    def check_password(self, attempt: str) -> bool:
        if not self.state.password_enabled:
            return True
        return attempt == self.state.password

    # --- Showfile ---

    async def load_showfile(self, showfile: Any) -> None:
        async with self._lock:
            from .models import Showfile
            self.state.showfile = showfile
            self.state.current_cue_index = 0
            self.state.paused = False
            self._arm_current_cue()
            self._update_cue_indicators()
            self._persist()
            await self._broadcast_positions_cue_info()
            await self._notify_caller_full_state()

    async def unload_showfile(self) -> None:
        async with self._lock:
            self.state.showfile = None
            self.state.current_cue_index = 0
            self.state.paused = False
            for pos in self.state.positions.values():
                pos.cue_indicator = ""
            self._persist()
            await self._broadcast_positions({"type": "cue_info", "scene": "", "cue_number": "", "note": ""})
            await self._notify_caller_full_state()

    async def jump_to_cue(self, index: int) -> None:
        async with self._lock:
            sf = self.state.showfile
            if not sf or index < 0 or index >= len(sf.cues):
                return
            self.state.current_cue_index = index
            self._arm_current_cue()
            self._update_cue_indicators()
            self._persist()
            await self._broadcast_positions_cue_info()
            await self._notify_caller_full_state()

    async def prev_cue(self) -> None:
        async with self._lock:
            if not self.state.showfile or self.state.current_cue_index <= 0:
                return
            self.state.current_cue_index -= 1
            self._arm_current_cue()
            self._update_cue_indicators()
            self._persist()
            await self._broadcast_positions_cue_info()
            await self._notify_caller_full_state()

    async def set_paused(self, paused: bool) -> None:
        async with self._lock:
            self.state.paused = paused
            self._persist()
            await self._notify_caller_full_state()

    # --- OSC patch ---

    async def load_patch(self, patch: OscPatch) -> list[str]:
        async with self._lock:
            self._clear_transient_osc_results()
            skipped: list[str] = []
            existing_labels = {
                p.label.lower() for p in self.state.positions.values()
                if p.type == PositionType.HUMAN
            }
            for device in patch.devices:
                if device.name.lower() in existing_labels:
                    skipped.append(device.name)
                    continue
                osc_id = f"osc:{device.name.lower()}"
                self.state.positions[osc_id] = Position(
                    client_id=osc_id,
                    label=device.name,
                    connected=True,
                    type=PositionType.OSC,
                    osc_probe=OscProbeState.UNVERIFIED,
                )
                self.osc_devices[osc_id] = device
            self.state.osc_patch_filename = patch.filename
            self._update_cue_indicators()
            self._persist()
            await self._notify_caller_full_state()
        self._start_osc_heartbeat()
        return skipped

    async def unload_patch(self) -> None:
        async with self._lock:
            self._clear_transient_osc_results()
            self._stop_osc_heartbeat()
            osc_ids = [cid for cid, p in self.state.positions.items() if p.type == PositionType.OSC]
            for cid in osc_ids:
                del self.state.positions[cid]
            self.osc_devices.clear()
            self.state.osc_patch_filename = ""
            self._update_cue_indicators()
            self._persist()
            await self._notify_caller_full_state()

    # --- OSC helpers (call under lock) ---

    def _clear_transient_osc_results(self) -> None:
        for pos in self.state.positions.values():
            if pos.osc_fire_result != OscFireResult.NONE:
                pos.osc_fire_result = OscFireResult.NONE

    def _get_cue_number_for_position(self, label: str) -> str:
        sf = self.state.showfile
        if not sf or self.state.current_cue_index >= len(sf.cues):
            return ""
        cue = sf.cues[self.state.current_cue_index]
        for target in cue.targets:
            if target.position.lower() == label.lower():
                return target.cue_number
        return ""

    async def _send_osc_result(self, client_id: str, result: str) -> None:
        if not self.caller_ws:
            return
        try:
            await self.caller_ws.send_json({
                "type": "osc_result",
                "client_id": client_id,
                "result": result,
            })
        except Exception:
            pass

    def _start_osc_heartbeat(self) -> None:
        self._stop_osc_heartbeat()
        if self.osc_devices:
            self._osc_heartbeat_task = asyncio.create_task(self._osc_heartbeat_loop())

    def _stop_osc_heartbeat(self) -> None:
        if self._osc_heartbeat_task:
            self._osc_heartbeat_task.cancel()
            self._osc_heartbeat_task = None

    async def _osc_heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(5.0)
                async with self._lock:
                    changed = False
                    for cid, device in list(self.osc_devices.items()):
                        pos = self.state.positions.get(cid)
                        if not pos:
                            continue
                        if pos.osc_trust not in ("osc_reply", "tcp_port"):
                            continue
                        try:
                            probe_state, trust = await osc_mod.probe(device)
                            if pos.osc_probe != probe_state or pos.osc_trust != trust:
                                pos.osc_probe = probe_state
                                pos.osc_trust = trust
                                changed = True
                        except Exception:
                            if pos.osc_probe != OscProbeState.FAILED:
                                pos.osc_probe = OscProbeState.FAILED
                                changed = True
                    if changed:
                        await self._notify_caller_full_state()
        except asyncio.CancelledError:
            pass

    # --- Cue helpers (call under lock) ---

    def _advance_cue(self) -> None:
        sf = self.state.showfile
        if not sf or self.state.paused:
            return
        if self.state.current_cue_index < len(sf.cues) - 1:
            self.state.current_cue_index += 1
            self._arm_current_cue()
            self._update_cue_indicators()

    def _arm_current_cue(self) -> None:
        sf = self.state.showfile
        if not sf:
            return
        for pos in self.state.positions.values():
            pos.armed = False
        cue = sf.cues[self.state.current_cue_index]
        label_to_pos = {p.label.lower(): p for p in self.state.positions.values()}
        for target in cue.targets:
            pos = label_to_pos.get(target.position.lower())
            if pos:
                pos.armed = True

    def _update_cue_indicators(self) -> None:
        for pos in self.state.positions.values():
            pos.cue_indicator = ""
        sf = self.state.showfile
        if not sf or self.state.current_cue_index >= len(sf.cues):
            return
        cue = sf.cues[self.state.current_cue_index]
        label_to_pos = {p.label.lower(): p for p in self.state.positions.values()}
        for target in cue.targets:
            pos = label_to_pos.get(target.position.lower())
            if pos:
                pos.cue_indicator = f"{target.position} {target.cue_number}"

    def _get_current_cue_info(self) -> dict[str, Any]:
        sf = self.state.showfile
        if not sf or self.state.current_cue_index >= len(sf.cues):
            return {}
        cue = sf.cues[self.state.current_cue_index]
        return {
            "scene": cue.scene,
            "cue_index": self.state.current_cue_index,
            "total_cues": len(sf.cues),
            "targets": [t.to_dict() for t in cue.targets],
            "note": cue.note,
        }

    # --- Health ---

    async def update_health(self, client_id: str, latency_ms: float) -> None:
        pos = self.state.positions.get(client_id)
        if not pos:
            return
        pos.latency_ms = latency_ms
        if latency_ms > 3000:
            pos.health = HealthStatus.RED
        elif latency_ms > 1000:
            pos.health = HealthStatus.YELLOW
        else:
            pos.health = HealthStatus.GREEN

    async def mark_unhealthy(self, client_id: str) -> None:
        pos = self.state.positions.get(client_id)
        if pos:
            pos.health = HealthStatus.RED

    # --- Exit ---

    async def exit_show(self) -> None:
        async with self._lock:
            self._stop_osc_heartbeat()
            await self._broadcast_positions({"type": "show_ended"})
            for ws in list(self.position_ws.values()):
                try:
                    await ws.close()
                except Exception:
                    pass
            self.position_ws.clear()
            self.osc_devices.clear()
            self.state = AppState()
            from .persistence import wipe_state
            wipe_state()

    # --- Messaging helpers ---

    async def _send_position(self, client_id: str, msg: dict) -> None:
        pos = self.state.positions.get(client_id)
        if pos and pos.type == PositionType.OSC:
            return
        ws = self.position_ws.get(client_id)
        if ws:
            try:
                await ws.send_json(msg)
            except Exception:
                pass

    async def _broadcast_positions(self, msg: dict) -> None:
        for ws in list(self.position_ws.values()):
            try:
                await ws.send_json(msg)
            except Exception:
                pass

    async def _broadcast_positions_cue_info(self) -> None:
        sf = self.state.showfile
        if not sf:
            return
        cue = sf.cues[self.state.current_cue_index] if self.state.current_cue_index < len(sf.cues) else None
        label_to_target = {}
        if cue:
            for t in cue.targets:
                label_to_target[t.position.lower()] = t

        for cid, pos in self.state.positions.items():
            target = label_to_target.get(pos.label.lower())
            msg = {
                "type": "cue_info",
                "scene": cue.scene if cue else "",
                "cue_number": target.cue_number if target else "",
                "note": cue.note if cue else "",
            }
            await self._send_position(cid, msg)

    async def _notify_caller_full_state(self) -> None:
        if not self.caller_ws:
            return
        cue_info = self._get_current_cue_info()
        missing = self._get_missing_positions()
        msg = {
            "type": "full_state",
            "positions": {k: v.to_dict() for k, v in self.state.positions.items()},
            "locked": self.state.locked,
            "showfile": self.state.showfile.to_dict() if self.state.showfile else None,
            "current_cue_index": self.state.current_cue_index,
            "paused": self.state.paused,
            "cue_info": cue_info,
            "missing_positions": missing,
            "password_enabled": self.state.password_enabled,
            "password": self.state.password,
            "osc_patch_filename": self.state.osc_patch_filename,
        }
        try:
            await self.caller_ws.send_json(msg)
        except Exception:
            pass

    def _get_missing_positions(self) -> list[str]:
        sf = self.state.showfile
        if not sf or self.state.current_cue_index >= len(sf.cues):
            return []
        cue = sf.cues[self.state.current_cue_index]
        connected_labels = {p.label.lower() for p in self.state.positions.values() if p.connected}
        missing = []
        for target in cue.targets:
            if target.position.lower() not in connected_labels:
                missing.append(target.position)
        return missing

    def get_full_state_for_caller(self) -> dict[str, Any]:
        cue_info = self._get_current_cue_info()
        missing = self._get_missing_positions()
        return {
            "type": "full_state",
            "positions": {k: v.to_dict() for k, v in self.state.positions.items()},
            "locked": self.state.locked,
            "showfile": self.state.showfile.to_dict() if self.state.showfile else None,
            "current_cue_index": self.state.current_cue_index,
            "paused": self.state.paused,
            "cue_info": cue_info,
            "missing_positions": missing,
            "password_enabled": self.state.password_enabled,
            "password": self.state.password,
            "osc_patch_filename": self.state.osc_patch_filename,
        }
