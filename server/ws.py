from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from .state import StateManager

HEARTBEAT_INTERVAL = 1.0
MISSED_PONG_LIMIT = 3


async def caller_ws_handler(ws: WebSocket, manager: StateManager) -> None:
    await ws.accept()
    data = await ws.receive_json()
    client_id = data.get("client_id", "")

    if not await manager.register_caller(ws, client_id):
        await ws.send_json({"type": "role_rejected", "reason": "caller_taken"})
        await ws.close()
        return

    await ws.send_json({"type": "role_assigned", "role": "caller"})
    state = manager.get_full_state_for_caller()
    await ws.send_json(state)

    heartbeat_task = asyncio.create_task(_caller_heartbeat(ws))
    try:
        await _caller_message_loop(ws, manager)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        heartbeat_task.cancel()
        await manager.unregister_caller()


async def _caller_heartbeat(ws: WebSocket) -> None:
    while True:
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await ws.send_json({"type": "ping", "ts": time.time()})
        except Exception:
            break


async def _caller_message_loop(ws: WebSocket, manager: StateManager) -> None:
    while True:
        msg = await ws.receive_json()
        msg_type = msg.get("type", "")

        if msg_type == "pong":
            pass

        elif msg_type == "standby":
            await manager.call_standby(msg["client_id"])

        elif msg_type == "go":
            await manager.call_go(msg["client_id"])

        elif msg_type == "standby_armed":
            await manager.standby_armed()

        elif msg_type == "go_armed":
            await manager.go_armed()

        elif msg_type == "reset_armed":
            await manager.reset_armed()

        elif msg_type == "reset_all":
            await manager.reset_all()

        elif msg_type == "toggle_arm":
            await manager.toggle_arm(msg["client_id"])

        elif msg_type == "rename":
            await manager.rename_position(msg["client_id"], msg["label"])

        elif msg_type == "lock":
            await manager.set_lock(msg["locked"])

        elif msg_type == "exit":
            await manager.exit_show()
            break

        elif msg_type == "set_password":
            await manager.set_password(msg.get("enabled", False), msg.get("password", ""))

        elif msg_type == "load_showfile":
            from .showfile import load_showfile
            try:
                sf = load_showfile(msg["filename"])
                await manager.load_showfile(sf)
            except Exception:
                await ws.send_json({"type": "error", "message": "Failed to load showfile"})

        elif msg_type == "unload_showfile":
            await manager.unload_showfile()

        elif msg_type == "jump_to_cue":
            await manager.jump_to_cue(msg["index"])

        elif msg_type == "prev_cue":
            await manager.prev_cue()

        elif msg_type == "pause":
            await manager.set_paused(msg["paused"])

        elif msg_type == "remove_position":
            await manager.remove_position(msg["client_id"])

        elif msg_type == "load_patch":
            from .patch import load_patch
            try:
                patch = load_patch(msg["filename"])
                skipped = await manager.load_patch(patch)
                if skipped:
                    await ws.send_json({"type": "patch_loaded", "skipped": skipped})
            except Exception:
                await ws.send_json({"type": "error", "message": "Failed to load patch"})

        elif msg_type == "unload_patch":
            await manager.unload_patch()


async def position_ws_handler(ws: WebSocket, manager: StateManager) -> None:
    await ws.accept()
    data = await ws.receive_json()
    client_id = data.get("client_id", "")
    label = data.get("label", "POS")

    result = await manager.register_position(ws, client_id, label)
    if result == "duplicate":
        await ws.send_json({"type": "join_rejected", "reason": f'Label "{label}" is already in use.'})
        await ws.close()
        return
    if not result:
        await ws.send_json({"type": "join_rejected", "reason": "Show is full (16 positions max)."})
        await ws.close()
        return

    pos = manager.state.positions.get(client_id)
    init_msg: dict[str, Any] = {
        "type": "joined",
        "label": pos.label if pos else label,
        "standby": pos.standby.value if pos else "idle",
        "go": pos.go.value if pos else "idle",
        "locked": manager.state.locked,
        "caller_connected": manager.state.caller_connected,
    }
    sf = manager.state.showfile
    if sf and pos:
        cue = sf.cues[manager.state.current_cue_index] if manager.state.current_cue_index < len(sf.cues) else None
        scene = cue.scene if cue else ""
        cue_num = ""
        if cue:
            for t in cue.targets:
                if t.position.lower() == pos.label.lower():
                    cue_num = t.cue_number
                    break
        init_msg["scene"] = scene
        init_msg["cue_number"] = cue_num
        init_msg["note"] = cue.note if cue else ""
    await ws.send_json(init_msg)

    missed_pongs = 0
    last_pong_ts = time.time()

    heartbeat_task = asyncio.create_task(
        _position_heartbeat(ws, client_id, manager)
    )
    try:
        await _position_message_loop(ws, client_id, manager)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        heartbeat_task.cancel()
        await manager.unregister_position(client_id)


async def _position_heartbeat(ws: WebSocket, client_id: str, manager: StateManager) -> None:
    missed = 0
    while True:
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            ts = time.time()
            await ws.send_json({"type": "ping", "ts": ts})
            # Check for pong within a short window
            missed += 1
            if missed >= MISSED_PONG_LIMIT:
                await manager.mark_unhealthy(client_id)
        except Exception:
            break


async def _position_message_loop(ws: WebSocket, client_id: str, manager: StateManager) -> None:
    while True:
        msg = await ws.receive_json()
        msg_type = msg.get("type", "")

        if msg_type == "pong":
            sent_ts = msg.get("ts", 0)
            latency = (time.time() - sent_ts) * 1000
            await manager.update_health(client_id, latency)
            await ws.send_json({"type": "health", "latency_ms": round(latency, 1),
                                "caller_connected": manager.state.caller_connected})

        elif msg_type == "ack_standby":
            await manager.ack_standby(client_id)

        elif msg_type == "ack_go":
            await manager.ack_go(client_id)

        elif msg_type == "rename":
            await manager.rename_position(client_id, msg["label"])

        elif msg_type == "disconnect":
            break
