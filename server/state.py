from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any

from fastapi import WebSocket

from . import osc as osc_mod
from .models import (
    AppState, ButtonState, HealthStatus, OscDevice, OscFireResult,
    OscPatch, OscProbeState, Position, PositionType,
)
from .persistence import save_state
from .showlog import ShowLog


COLOR_PALETTE = [
    "#5b8def",  # Blue
    "#a855f7",  # Purple
    "#f97316",  # Orange
    "#ec4899",  # Pink
    "#06b6d4",  # Cyan
    "#6366f1",  # Indigo
    "#64748b",  # Slate
    "#d946ef",  # Fuchsia
]


class StateManager:
    MAX_POSITIONS = 16
    ATTENTION_MESSAGE_MAX = 120

    def __init__(self, initial: AppState | None = None) -> None:
        self.state = initial or AppState()
        self.caller_ws: WebSocket | None = None
        self.position_ws: dict[str, WebSocket] = {}
        self.observer_ws: dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()
        self.osc_devices: dict[str, OscDevice] = {}
        self._osc_heartbeat_task: asyncio.Task | None = None
        self._osc_tasks: set[asyncio.Task] = set()
        self.log = ShowLog()

    def _persist(self) -> None:
        save_state(self.state)

    def _next_color(self) -> str:
        used = {p.color for p in self.state.positions.values() if p.color}
        for c in COLOR_PALETTE:
            if c not in used:
                return c
        return COLOR_PALETTE[len(self.state.positions) % len(COLOR_PALETTE)]

    # --- Caller management ---

    async def register_caller(self, ws: WebSocket, client_id: str) -> bool:
        async with self._lock:
            if self.state.caller_connected:
                # Same device reconnecting (e.g. after a silent drop) takes
                # over from its stale socket; anyone else is rejected.
                if not client_id or client_id != self.state.caller_client_id:
                    return False
                old_ws = self.caller_ws
                if old_ws is not None and old_ws is not ws:
                    try:
                        await old_ws.close()
                    except Exception:
                        pass
            self.state.caller_connected = True
            self.state.caller_client_id = client_id
            self.caller_ws = ws
            self.log.record("caller_connected")
            self._persist()
            await self._send_observers_full_state()
            return True

    async def unregister_caller(self, ws: WebSocket | None = None) -> None:
        async with self._lock:
            # A stale socket closing after a takeover must not unregister the new caller
            if ws is not None and self.caller_ws is not None and self.caller_ws is not ws:
                return
            self.state.caller_connected = False
            self.caller_ws = None
            self.log.record("caller_disconnected")
            self._persist()
            await self._broadcast_positions({"type": "caller_disconnected"})
            await self._send_observers_full_state()

    # --- Observer management ---

    async def register_observer(self, ws: WebSocket, client_id: str, password: str = "") -> bool:
        if self.state.password_enabled and not self.check_password(password):
            return False
        async with self._lock:
            self.observer_ws[client_id or f"obs:{id(ws)}"] = ws
            self.log.record("observer_connected")
            return True

    async def unregister_observer(self, ws: WebSocket) -> None:
        async with self._lock:
            for cid, w in list(self.observer_ws.items()):
                if w is ws:
                    del self.observer_ws[cid]
                    self.log.record("observer_disconnected")
                    break

    # --- Position management ---

    async def register_position(self, ws: WebSocket, client_id: str, label: str) -> bool | str:
        async with self._lock:
            # Uniqueness must hold for reconnects too: a known client_id coming
            # back with a changed label may not take another position's label.
            for cid, p in self.state.positions.items():
                if cid != client_id and p.label.lower() == label.lower():
                    return "duplicate"
            if client_id in self.state.positions:
                pos = self.state.positions[client_id]
                pos.connected = True
                pos.label = label
                pos.health = HealthStatus.GREEN
                if not pos.color:
                    pos.color = self._next_color()
            else:
                if len(self.state.positions) >= self.MAX_POSITIONS:
                    return False
                self.state.positions[client_id] = Position(
                    client_id=client_id, label=label, color=self._next_color()
                )
            self.log.record("position_joined", position=label, cue=self._current_cue_seq())
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
                self.log.record(
                    "position_disconnected",
                    position=self.state.positions[client_id].label,
                    cue=self._current_cue_seq(),
                )
            self.position_ws.pop(client_id, None)
            self._persist()
            await self._notify_caller_full_state()

    async def remove_position(self, client_id: str) -> None:
        async with self._lock:
            removed = self.state.positions.pop(client_id, None)
            if removed:
                self.log.record("position_removed", position=removed.label)
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
                    self._spawn_osc_task(self._probe_and_record([(client_id, device)]))
                self._persist()
                await self._notify_caller_full_state()
            else:
                pos.standby = ButtonState.CALLED
                pos.armed = False
                self.log.record("standby_called", position=pos.label, cue=self._current_cue_seq())
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
            self.log.record("standby_acked", position=pos.label, cue=self._current_cue_seq())
            self._persist()
            await self._notify_caller_full_state()

    async def call_go(self, client_id: str) -> None:
        async with self._lock:
            pos = self.state.positions.get(client_id)
            if not pos or self.state.locked:
                return
            self._clear_transient_osc_results()
            self.state.last_go_at = time.time()
            if pos.type == PositionType.OSC:
                pos.standby = ButtonState.IDLE
                pos.armed = False
                device = self.osc_devices.get(client_id)
                if device:
                    cue_number = self._get_cue_number_for_position(pos.label)
                    self._spawn_osc_task(self._fire_and_record([(client_id, device, cue_number)]))
                self._persist()
                await self._notify_caller_full_state()
            else:
                pos.standby = ButtonState.IDLE
                pos.go = ButtonState.CALLED
                pos.armed = False
                self.log.record("go_called", position=pos.label, cue=self._current_cue_seq())
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
            self.log.record("go_acked", position=pos.label, cue=self._current_cue_seq())
            self._persist()
            await self._notify_caller_full_state()

    # --- Master actions ---

    async def standby_armed(self) -> None:
        async with self._lock:
            if self.state.locked:
                return
            self._clear_transient_osc_results()
            self.log.record("master_standby", cue=self._current_cue_seq())
            probe_jobs: list[tuple[str, OscDevice]] = []
            for pos in self.state.positions.values():
                if pos.armed and pos.connected:
                    if pos.type == PositionType.OSC:
                        device = self.osc_devices.get(pos.client_id)
                        if device:
                            pos.osc_probe = OscProbeState.PROBING
                            probe_jobs.append((pos.client_id, device))
                    else:
                        pos.standby = ButtonState.CALLED
                        self.log.record("standby_called", position=pos.label, cue=self._current_cue_seq())
                        await self._send_position(pos.client_id, {"type": "standby_called"})
            if probe_jobs:
                self._spawn_osc_task(self._probe_and_record(probe_jobs))
            self._persist()
            await self._notify_caller_full_state()

    async def go_armed(self) -> None:
        async with self._lock:
            if self.state.locked:
                return
            self._clear_transient_osc_results()
            self.state.last_go_at = time.time()
            self.log.record("master_go", cue=self._current_cue_seq())
            fire_jobs: list[tuple[str, OscDevice, str]] = []
            for pos in self.state.positions.values():
                if pos.armed and pos.connected:
                    if pos.type == PositionType.OSC:
                        pos.standby = ButtonState.IDLE
                        pos.armed = False
                        device = self.osc_devices.get(pos.client_id)
                        if device:
                            # Resolve the cue number before _advance_cue moves on below
                            cue_number = self._get_cue_number_for_position(pos.label)
                            fire_jobs.append((pos.client_id, device, cue_number))
                    else:
                        pos.standby = ButtonState.IDLE
                        pos.go = ButtonState.CALLED
                        pos.armed = False
                        self.log.record("go_called", position=pos.label, cue=self._current_cue_seq())
                        await self._send_position(pos.client_id, {"type": "go_called"})
            advanced = self._advance_cue()
            if advanced:
                self.log.record("cue_advanced", cue=self._current_cue_seq())
            probe_jobs: list[tuple[str, OscDevice]] = []
            if advanced and self.state.auto_standby:
                # Warn the next cue's targets right away
                for pos in self.state.positions.values():
                    if not (pos.armed and pos.connected):
                        continue
                    if pos.type == PositionType.OSC:
                        device = self.osc_devices.get(pos.client_id)
                        if device:
                            pos.osc_probe = OscProbeState.PROBING
                            probe_jobs.append((pos.client_id, device))
                    elif pos.standby == ButtonState.IDLE:
                        pos.standby = ButtonState.CALLED
                        self.log.record("standby_called", position=pos.label,
                                        cue=self._current_cue_seq(), detail="auto")
                        await self._send_position(pos.client_id, {"type": "standby_called"})
            if fire_jobs:
                self._spawn_osc_task(self._fire_and_record(fire_jobs))
            if probe_jobs:
                self._spawn_osc_task(self._probe_and_record(probe_jobs))
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

    # --- Flash roll call ---

    async def flash_all(self) -> None:
        async with self._lock:
            if self.state.locked:
                return
            self.log.record("flash_all")
            for pos in self.state.positions.values():
                if pos.type == PositionType.HUMAN and pos.connected:
                    pos.flash = "pending"
                    await self._send_position(pos.client_id, {"type": "flash"})
                else:
                    pos.flash = "none"
            self._persist()
            await self._notify_caller_full_state()

    async def ack_flash(self, client_id: str) -> None:
        async with self._lock:
            pos = self.state.positions.get(client_id)
            if not pos or pos.flash != "pending":
                return
            pos.flash = "confirmed"
            self.log.record("flash_confirmed", position=pos.label)
            self._persist()
            await self._notify_caller_full_state()

    async def clear_flash(self) -> None:
        async with self._lock:
            for pos in self.state.positions.values():
                pos.flash = "none"
            self._persist()
            await self._notify_caller_full_state()

    # --- Operator attention ---

    async def raise_attention(self, client_id: str, message: str = "") -> None:
        # Deliberately NOT blocked by the lock: an operator reporting a
        # problem must get through even during a hold.
        async with self._lock:
            pos = self.state.positions.get(client_id)
            if not pos or pos.type != PositionType.HUMAN:
                return
            pos.attention = True
            pos.attention_message = str(message or "")[: self.ATTENTION_MESSAGE_MAX]
            self.log.record("attention_raised", position=pos.label,
                            cue=self._current_cue_seq(), detail=pos.attention_message)
            self._persist()
            await self._notify_caller_full_state()

    async def clear_attention(self, client_id: str, by_caller: bool = False) -> None:
        async with self._lock:
            pos = self.state.positions.get(client_id)
            if not pos or not pos.attention:
                return
            pos.attention = False
            pos.attention_message = ""
            self.log.record("attention_cleared", position=pos.label,
                            detail="caller" if by_caller else "operator")
            self._persist()
            if by_caller:
                # Tell the operator their report was seen
                await self._send_position(client_id, {"type": "attention_cleared"})
            await self._notify_caller_full_state()

    # --- Show clock ---

    async def start_show_clock(self) -> None:
        async with self._lock:
            self.state.show_started_at = time.time()
            self.log.record("show_started")
            self._persist()
            await self._broadcast_positions({"type": "show_started"})
            await self._notify_caller_full_state()

    async def clear_show_clock(self) -> None:
        async with self._lock:
            if not self.state.show_started_at:
                return
            self.state.show_started_at = 0.0
            self.log.record("show_clock_cleared")
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
            self.log.record("position_renamed", position=new_label, detail=f"was {pos.label}")
            pos.label = new_label
            self._update_cue_indicators()
            self._persist()
            await self._send_position(client_id, {
                "type": "label_changed",
                "label": new_label,
            })
            await self._notify_caller_full_state()
            return True

    # --- Color ---

    async def set_color(self, client_id: str, color: str) -> None:
        async with self._lock:
            pos = self.state.positions.get(client_id)
            if not pos:
                return
            if color not in COLOR_PALETTE:
                return
            pos.color = color
            self._persist()
            await self._send_position(client_id, {
                "type": "color_changed",
                "color": color,
            })
            await self._notify_caller_full_state()

    # --- Lock ---

    async def set_lock(self, locked: bool) -> None:
        async with self._lock:
            self.state.locked = locked
            self.log.record("locked" if locked else "unlocked")
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
        if not isinstance(attempt, str):
            return False
        # Compared as bytes: compare_digest rejects non-ASCII str arguments
        return secrets.compare_digest(attempt.encode(), self.state.password.encode())

    # --- Showfile ---

    async def load_showfile(self, showfile: Any) -> None:
        async with self._lock:
            self.state.showfile = showfile
            self.state.showfile_filename = showfile.filename
            self.state.current_cue_index = 0
            self.state.paused = False
            self.log.record("showfile_loaded", detail=showfile.filename)
            self._arm_current_cue()
            self._update_cue_indicators()
            self._persist()
            await self._broadcast_positions_cue_info()
            await self._notify_caller_full_state()

    async def restore_showfile(self, showfile: Any) -> None:
        """Startup restore: unlike load_showfile, keeps the persisted cue index."""
        async with self._lock:
            self.state.showfile = showfile
            self.state.showfile_filename = showfile.filename
            max_index = max(len(showfile.cues) - 1, 0)
            self.state.current_cue_index = min(self.state.current_cue_index, max_index)
            if showfile.cues:
                self._arm_current_cue()
            self._update_cue_indicators()
            self._persist()
            await self._broadcast_positions_cue_info()
            await self._notify_caller_full_state()

    async def clear_showfile_filename(self) -> None:
        async with self._lock:
            self.state.showfile_filename = ""
            self._persist()

    async def unload_showfile(self) -> None:
        async with self._lock:
            if self.state.showfile:
                self.log.record("showfile_unloaded", detail=self.state.showfile_filename)
            self.state.showfile = None
            self.state.showfile_filename = ""
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
            self.log.record("cue_jumped", cue=self._current_cue_seq())
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
            self.log.record("cue_jumped", cue=self._current_cue_seq())
            self._arm_current_cue()
            self._update_cue_indicators()
            self._persist()
            await self._broadcast_positions_cue_info()
            await self._notify_caller_full_state()

    async def set_paused(self, paused: bool) -> None:
        async with self._lock:
            self.state.paused = paused
            self.log.record("paused" if paused else "resumed", cue=self._current_cue_seq())
            self._persist()
            await self._notify_caller_full_state()

    async def set_auto_standby(self, enabled: bool) -> None:
        async with self._lock:
            self.state.auto_standby = bool(enabled)
            self.log.record("auto_standby_on" if enabled else "auto_standby_off")
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
                    color=self._next_color(),
                )
                self.osc_devices[osc_id] = device
            self.state.osc_patch_filename = patch.filename
            self.log.record("patch_loaded", detail=patch.filename)
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
            if self.state.osc_patch_filename:
                self.log.record("patch_unloaded", detail=self.state.osc_patch_filename)
            self.state.osc_patch_filename = ""
            self._update_cue_indicators()
            self._persist()
            await self._notify_caller_full_state()

    async def probe_test(self, data: dict[str, Any]) -> dict[str, str]:
        device = OscDevice(
            name=data.get("name", "test"),
            ip=data.get("ip", ""),
            port=data.get("port", 8000),
            protocol=data.get("protocol", "udp"),
            ping_template=data.get("ping_template", ""),
            expect_reply=data.get("expect_reply", False),
        )
        # Probes an ad-hoc device from the editor; touches no state, so no lock
        probe_state, trust = await osc_mod.probe(device)
        return {"probe": probe_state.value, "trust": trust}

    async def clear_osc_patch_filename(self) -> None:
        async with self._lock:
            self.state.osc_patch_filename = ""
            self._persist()

    # --- OSC I/O (runs outside the lock) ---

    def _spawn_osc_task(self, coro: Any) -> None:
        """Runs OSC network I/O in the background so the lock is never held
        across a fire/probe timeout."""
        task = asyncio.create_task(coro)
        self._osc_tasks.add(task)
        task.add_done_callback(self._osc_tasks.discard)

    async def _probe_and_record(self, jobs: list[tuple[str, OscDevice]]) -> None:
        """Probes concurrently, then re-acquires the lock to record results.
        Must never be awaited while the lock is held."""
        results = await asyncio.gather(
            *(osc_mod.probe(device) for _, device in jobs), return_exceptions=True
        )
        async with self._lock:
            changed = False
            for (client_id, _), result in zip(jobs, results):
                pos = self.state.positions.get(client_id)
                if not pos or pos.type != PositionType.OSC:
                    continue
                if isinstance(result, tuple):
                    probe_state, trust = result
                else:
                    probe_state, trust = OscProbeState.FAILED, pos.osc_trust
                if pos.osc_probe != probe_state or pos.osc_trust != trust:
                    pos.osc_probe = probe_state
                    pos.osc_trust = trust
                    changed = True
            if changed:
                self._persist()
                await self._notify_caller_full_state()

    async def _fire_and_record(self, jobs: list[tuple[str, OscDevice, str]]) -> None:
        """Fires concurrently, then re-acquires the lock to record results.
        Must never be awaited while the lock is held."""
        results = await asyncio.gather(
            *(osc_mod.fire(device, cue_number) for _, device, cue_number in jobs),
            return_exceptions=True,
        )
        async with self._lock:
            recorded = False
            for (client_id, _, cue_number), result in zip(jobs, results):
                pos = self.state.positions.get(client_id)
                if not pos or pos.type != PositionType.OSC:
                    continue
                fire_result = result if isinstance(result, OscFireResult) else OscFireResult.NO_REPLY
                pos.osc_fire_result = fire_result
                self.log.record("osc_fired", position=pos.label, cue=cue_number, detail=fire_result.value)
                await self._send_osc_result(client_id, fire_result.value)
                recorded = True
            if recorded:
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
                # 5s interval: less aggressive than the 1s position heartbeat to reduce network load on OSC gear
                await asyncio.sleep(5.0)
                async with self._lock:
                    jobs = [
                        (cid, device)
                        for cid, device in self.osc_devices.items()
                        if (pos := self.state.positions.get(cid))
                        and pos.osc_trust in ("osc_reply", "tcp_port")
                    ]
                # Probe outside the lock; _probe_and_record re-acquires it briefly
                if jobs:
                    await self._probe_and_record(jobs)
        except asyncio.CancelledError:
            pass

    # --- Cue helpers (call under lock) ---

    def _advance_cue(self) -> bool:
        sf = self.state.showfile
        if not sf or self.state.paused:
            return False
        if self.state.current_cue_index < len(sf.cues) - 1:
            self.state.current_cue_index += 1
            self._arm_current_cue()
            self._update_cue_indicators()
            return True
        return False

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

    def _current_cue_seq(self) -> str:
        sf = self.state.showfile
        if not sf or self.state.current_cue_index >= len(sf.cues):
            return ""
        return str(sf.cues[self.state.current_cue_index].sequence)

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
        async with self._lock:
            pos = self.state.positions.get(client_id)
            if not pos:
                return
            pos.latency_ms = latency_ms
            if latency_ms > 3000:
                health = HealthStatus.RED
            elif latency_ms > 1000:
                health = HealthStatus.YELLOW
            else:
                health = HealthStatus.GREEN
            # Only push on tier changes, not on every latency sample
            if health != pos.health:
                pos.health = health
                await self._notify_caller_full_state()

    async def mark_unhealthy(self, client_id: str) -> None:
        async with self._lock:
            pos = self.state.positions.get(client_id)
            if pos and pos.health != HealthStatus.RED:
                pos.health = HealthStatus.RED
                await self._notify_caller_full_state()

    # --- Exit ---

    async def exit_show(self) -> None:
        async with self._lock:
            self._stop_osc_heartbeat()
            for task in list(self._osc_tasks):
                task.cancel()
            self._osc_tasks.clear()
            await self._broadcast_positions({"type": "show_ended"})
            for ws in list(self.position_ws.values()):
                try:
                    await ws.close()
                except Exception:
                    pass
            self.position_ws.clear()
            # Observers get their sockets closed and auto-reconnect into the new state
            for ws in list(self.observer_ws.values()):
                try:
                    await ws.close()
                except Exception:
                    pass
            self.observer_ws.clear()
            self.osc_devices.clear()
            self.state = AppState()
            self.log.record("show_ended")
            from .persistence import wipe_state
            wipe_state()
            self.log.rotate()

    # --- Resume ---

    async def resume_show(self) -> bool:
        """Swaps in the show archived by EXIT (snapshot.bak). The caller's
        connection is kept; live position sockets are closed so the devices
        auto-reconnect and merge into the restored state. The archived
        showfile/patch are reloaded by the caller of this method."""
        from .persistence import restore_backup
        async with self._lock:
            restored = restore_backup()
            if restored is None:
                return False
            self._stop_osc_heartbeat()
            self.osc_devices.clear()
            restored.caller_connected = self.state.caller_connected
            restored.caller_client_id = self.state.caller_client_id
            self.state = restored
            for ws in list(self.position_ws.values()):
                try:
                    await ws.close()
                except Exception:
                    pass
            self.position_ws.clear()
            self.log.restore()
            self.log.record("show_resumed")
            self._persist()
            await self._notify_caller_full_state()
            return True

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
        if self.caller_ws:
            msg = self.get_full_state_for_caller()
            try:
                await self.caller_ws.send_json(msg)
            except Exception:
                pass
        await self._send_observers_full_state()

    async def _send_observers_full_state(self) -> None:
        if not self.observer_ws:
            return
        msg = self.get_full_state_for_observer()
        for ws in list(self.observer_ws.values()):
            try:
                await ws.send_json(msg)
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
            "auto_standby": self.state.auto_standby,
            "caller_connected": self.state.caller_connected,
            "cue_info": cue_info,
            "missing_positions": missing,
            "password_enabled": self.state.password_enabled,
            "password": self.state.password,
            "osc_patch_filename": self.state.osc_patch_filename,
            "show_started_at": self.state.show_started_at,
            "last_go_at": self.state.last_go_at,
        }

    def get_full_state_for_observer(self) -> dict[str, Any]:
        """Same as the caller's view, but never leaks the join password."""
        msg = self.get_full_state_for_caller()
        msg["password"] = ""
        return msg
