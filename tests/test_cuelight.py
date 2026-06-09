"""
Integration tests for CueLight.

Spins up a real uvicorn server on port 8001 and tests via WebSocket connections.
Run with: python3 tests/test_cuelight.py
    or:   python3 -m pytest tests/test_cuelight.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import unittest
from pathlib import Path
from urllib import request as urllib_request

import uvicorn
from websockets.asyncio.client import connect
import websockets.exceptions

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_SNAPSHOT = PROJECT_ROOT / "state" / "snapshot.json"
TEST_PORT = 8001
WS_URL = f"ws://localhost:{TEST_PORT}"
HTTP_URL = f"http://localhost:{TEST_PORT}"


def _clean_state():
    if STATE_SNAPSHOT.exists():
        STATE_SNAPSHOT.unlink()


class _ServerThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.server = None

    def run(self):
        import importlib
        import server.main
        importlib.reload(server.main)

        config = uvicorn.Config(
            server.main.app,
            host="127.0.0.1",
            port=TEST_PORT,
            log_level="error",
        )
        self.server = uvicorn.Server(config)
        self.server.run()

    def stop(self):
        if self.server:
            self.server.should_exit = True


_server_thread: _ServerThread | None = None


def setUpModule():
    global _server_thread
    _clean_state()
    _server_thread = _ServerThread()
    _server_thread.start()
    for _ in range(40):
        try:
            urllib_request.urlopen(f"{HTTP_URL}/api/info", timeout=1)
            return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("Server failed to start")


def tearDownModule():
    global _server_thread
    if _server_thread:
        _server_thread.stop()
        _server_thread.join(timeout=3)
    _clean_state()


def _reset_server():
    req = urllib_request.Request(
        f"{HTTP_URL}/api/_test_reset",
        data=b"",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib_request.urlopen(req, timeout=2)
    time.sleep(0.1)


# --- Helpers ---

async def connect_caller(client_id: str = "test-caller"):
    ws = await connect(f"{WS_URL}/ws/caller")
    await ws.send(json.dumps({"client_id": client_id}))
    role = json.loads(await ws.recv())
    state = json.loads(await ws.recv())
    return ws, role, state


async def connect_position(client_id: str, label: str):
    ws = await connect(f"{WS_URL}/ws/position")
    await ws.send(json.dumps({"client_id": client_id, "label": label}))
    msg = json.loads(await ws.recv())
    return ws, msg


async def recv_type(ws, expected_type: str, timeout: float = 2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
        except asyncio.TimeoutError:
            break
        msg = json.loads(raw)
        if msg.get("type") == expected_type:
            return msg
    raise AssertionError(f"Did not receive '{expected_type}' within {timeout}s")


async def drain(ws, count: int = 20, timeout: float = 0.3):
    msgs = []
    for _ in range(count):
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            msgs.append(json.loads(raw))
        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
            break
    return msgs


# --- Tests ---

class CueLightTestCase(unittest.TestCase):
    """Base class that resets server state before each test."""

    def setUp(self):
        _reset_server()


class TestCallerConnection(CueLightTestCase):
    def test_first_connection_becomes_caller(self):
        async def run():
            ws, role, state = await connect_caller()
            self.assertEqual(role["type"], "role_assigned")
            self.assertEqual(role["role"], "caller")
            self.assertEqual(state["type"], "full_state")
            await ws.close()
        asyncio.run(run())

    def test_second_caller_rejected(self):
        async def run():
            ws1, _, _ = await connect_caller("c1")
            ws2 = await connect(f"{WS_URL}/ws/caller")
            await ws2.send(json.dumps({"client_id": "c2"}))
            msg = json.loads(await ws2.recv())
            self.assertEqual(msg["type"], "role_rejected")
            await ws2.close()
            await ws1.close()
        asyncio.run(run())

    def test_caller_reconnect_after_disconnect(self):
        async def run():
            ws1, _, _ = await connect_caller("c1")
            await ws1.close()
            await asyncio.sleep(0.3)
            ws2, role, _ = await connect_caller("c2")
            self.assertEqual(role["type"], "role_assigned")
            await ws2.close()
        asyncio.run(run())


class TestPositionConnection(CueLightTestCase):
    def test_position_join(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, msg = await connect_position("p1", "LX")
            self.assertEqual(msg["type"], "joined")
            self.assertEqual(msg["label"], "LX")
            state = await recv_type(cws, "full_state")
            self.assertIn("p1", state["positions"])
            self.assertEqual(state["positions"]["p1"]["label"], "LX")
            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_duplicate_label_rejected(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws1, _ = await connect_position("p1", "LX")
            await drain(cws)
            pws2, msg = await connect_position("p2", "LX")
            self.assertEqual(msg["type"], "join_rejected")
            self.assertIn("already in use", msg["reason"])
            await pws1.close()
            await cws.close()
        asyncio.run(run())

    def test_duplicate_label_case_insensitive(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws1, _ = await connect_position("p1", "SND")
            await drain(cws)
            pws2, msg = await connect_position("p2", "snd")
            self.assertEqual(msg["type"], "join_rejected")
            await pws1.close()
            await cws.close()
        asyncio.run(run())

    def test_same_client_id_reconnects(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws1, msg1 = await connect_position("p1", "FLY")
            self.assertEqual(msg1["type"], "joined")
            await pws1.close()
            await asyncio.sleep(0.3)
            await drain(cws)
            pws2, msg2 = await connect_position("p1", "FLY")
            self.assertEqual(msg2["type"], "joined")
            await pws2.close()
            await cws.close()
        asyncio.run(run())

    def test_max_positions_rejected(self):
        async def run():
            cws, _, _ = await connect_caller()
            positions = []
            for i in range(16):
                pws, msg = await connect_position(f"p{i}", f"POS{i}")
                self.assertEqual(msg["type"], "joined", f"Position {i} failed to join")
                positions.append(pws)
                await drain(cws)

            pws17, msg = await connect_position("p16", "POS16")
            self.assertEqual(msg["type"], "join_rejected")
            self.assertIn("full", msg["reason"])

            for p in positions:
                await p.close()
            await cws.close()
        asyncio.run(run())


class TestButtonStateMachine(CueLightTestCase):
    def test_standby_go_ack_cycle(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            # Call standby
            await cws.send(json.dumps({"type": "standby", "client_id": "p1"}))
            await recv_type(pws, "standby_called")
            state = await recv_type(cws, "full_state")
            self.assertEqual(state["positions"]["p1"]["standby"], "called")

            # Ack standby
            await pws.send(json.dumps({"type": "ack_standby"}))
            state = await recv_type(cws, "full_state")
            self.assertEqual(state["positions"]["p1"]["standby"], "acked")

            # Call GO
            await cws.send(json.dumps({"type": "go", "client_id": "p1"}))
            await recv_type(pws, "go_called")
            state = await recv_type(cws, "full_state")
            self.assertEqual(state["positions"]["p1"]["go"], "called")
            self.assertEqual(state["positions"]["p1"]["standby"], "idle")

            # Ack GO
            await pws.send(json.dumps({"type": "ack_go"}))
            state = await recv_type(cws, "full_state")
            self.assertEqual(state["positions"]["p1"]["go"], "idle")

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_standby_ack_ignored_when_idle(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await pws.send(json.dumps({"type": "ack_standby"}))
            await asyncio.sleep(0.3)
            msgs = await drain(cws)
            for m in msgs:
                if m.get("type") == "full_state":
                    self.assertEqual(m["positions"]["p1"]["standby"], "idle")

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_go_ack_ignored_when_idle(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await pws.send(json.dumps({"type": "ack_go"}))
            await asyncio.sleep(0.3)
            msgs = await drain(cws)
            for m in msgs:
                if m.get("type") == "full_state":
                    self.assertEqual(m["positions"]["p1"]["go"], "idle")

            await pws.close()
            await cws.close()
        asyncio.run(run())


class TestMasterControls(CueLightTestCase):
    def test_standby_armed(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws1, _ = await connect_position("p1", "LX")
            await drain(cws)
            pws2, _ = await connect_position("p2", "SND")
            await drain(cws)

            await cws.send(json.dumps({"type": "toggle_arm", "client_id": "p1"}))
            await drain(cws)
            await cws.send(json.dumps({"type": "toggle_arm", "client_id": "p2"}))
            await drain(cws)

            await cws.send(json.dumps({"type": "standby_armed"}))
            msg1 = await recv_type(pws1, "standby_called")
            msg2 = await recv_type(pws2, "standby_called")
            self.assertEqual(msg1["type"], "standby_called")
            self.assertEqual(msg2["type"], "standby_called")

            await pws1.close()
            await pws2.close()
            await cws.close()
        asyncio.run(run())

    def test_go_armed(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await cws.send(json.dumps({"type": "toggle_arm", "client_id": "p1"}))
            await drain(cws)

            await cws.send(json.dumps({"type": "go_armed"}))
            msg = await recv_type(pws, "go_called")
            self.assertEqual(msg["type"], "go_called")
            state = await recv_type(cws, "full_state")
            self.assertEqual(state["positions"]["p1"]["go"], "called")
            self.assertFalse(state["positions"]["p1"]["armed"])

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_reset_armed(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await cws.send(json.dumps({"type": "toggle_arm", "client_id": "p1"}))
            await drain(cws)
            await cws.send(json.dumps({"type": "standby_armed"}))
            await drain(pws)
            await drain(cws)

            await cws.send(json.dumps({"type": "reset_armed"}))
            msg = await recv_type(pws, "state_reset")
            self.assertEqual(msg["type"], "state_reset")
            state = await recv_type(cws, "full_state")
            self.assertFalse(state["positions"]["p1"]["armed"])
            self.assertEqual(state["positions"]["p1"]["standby"], "idle")

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_individual_button_disarms(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await cws.send(json.dumps({"type": "toggle_arm", "client_id": "p1"}))
            state = await recv_type(cws, "full_state")
            self.assertTrue(state["positions"]["p1"]["armed"])

            await cws.send(json.dumps({"type": "standby", "client_id": "p1"}))
            await drain(pws)
            state = await recv_type(cws, "full_state")
            self.assertFalse(state["positions"]["p1"]["armed"])

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_unarmed_not_affected_by_master(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws1, _ = await connect_position("p1", "LX")
            await drain(cws)
            pws2, _ = await connect_position("p2", "SND")
            await drain(cws)

            # Only arm p1
            await cws.send(json.dumps({"type": "toggle_arm", "client_id": "p1"}))
            await drain(cws)

            await cws.send(json.dumps({"type": "standby_armed"}))
            await recv_type(pws1, "standby_called")
            # p2 should not receive standby_called
            await asyncio.sleep(0.3)
            msgs = await drain(pws2, timeout=0.3)
            types = [m.get("type") for m in msgs]
            self.assertNotIn("standby_called", types)

            await pws1.close()
            await pws2.close()
            await cws.close()
        asyncio.run(run())


class TestLock(CueLightTestCase):
    def test_lock_prevents_actions(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await cws.send(json.dumps({"type": "lock", "locked": True}))
            lock_msg = await recv_type(pws, "lock_changed")
            self.assertTrue(lock_msg["locked"])
            await drain(cws)

            await cws.send(json.dumps({"type": "standby", "client_id": "p1"}))
            await asyncio.sleep(0.3)
            msgs = await drain(pws, timeout=0.3)
            types = [m.get("type") for m in msgs]
            self.assertNotIn("standby_called", types)

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_lock_and_unlock(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await cws.send(json.dumps({"type": "lock", "locked": True}))
            msg = await recv_type(pws, "lock_changed")
            self.assertTrue(msg["locked"])
            await drain(cws)

            await cws.send(json.dumps({"type": "lock", "locked": False}))
            msg = await recv_type(pws, "lock_changed")
            self.assertFalse(msg["locked"])

            await pws.close()
            await cws.close()
        asyncio.run(run())


class TestShowfile(CueLightTestCase):
    def test_load_and_auto_arm(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws_lx, _ = await connect_position("p1", "LX")
            await drain(cws)
            pws_snd, _ = await connect_position("p2", "SND")
            await drain(cws)

            await cws.send(json.dumps({"type": "load_showfile", "filename": "example.json"}))
            lx_cue = await recv_type(pws_lx, "cue_info")
            snd_cue = await recv_type(pws_snd, "cue_info")
            self.assertEqual(lx_cue["cue_number"], "1")
            self.assertEqual(snd_cue["cue_number"], "1")

            state = await recv_type(cws, "full_state")
            self.assertIsNotNone(state["showfile"])
            self.assertTrue(state["positions"]["p1"]["armed"])
            self.assertTrue(state["positions"]["p2"]["armed"])

            await pws_lx.close()
            await pws_snd.close()
            await cws.close()
        asyncio.run(run())

    def test_go_armed_advances_cue(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await cws.send(json.dumps({"type": "load_showfile", "filename": "example.json"}))
            await drain(pws)
            state = await recv_type(cws, "full_state")
            self.assertEqual(state["current_cue_index"], 0)

            await cws.send(json.dumps({"type": "go_armed"}))
            await drain(pws)
            state = await recv_type(cws, "full_state")
            self.assertEqual(state["current_cue_index"], 1)

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_prev_cue(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await cws.send(json.dumps({"type": "load_showfile", "filename": "example.json"}))
            await drain(pws)
            await drain(cws)

            await cws.send(json.dumps({"type": "go_armed"}))
            await drain(pws)
            state = await recv_type(cws, "full_state")
            self.assertEqual(state["current_cue_index"], 1)

            await cws.send(json.dumps({"type": "prev_cue"}))
            await drain(pws)
            state = await recv_type(cws, "full_state")
            self.assertEqual(state["current_cue_index"], 0)

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_jump_to_cue(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await cws.send(json.dumps({"type": "load_showfile", "filename": "example.json"}))
            await drain(pws)
            await drain(cws)

            await cws.send(json.dumps({"type": "jump_to_cue", "index": 3}))
            await drain(pws)
            state = await recv_type(cws, "full_state")
            self.assertEqual(state["current_cue_index"], 3)

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_pause_prevents_auto_advance(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await cws.send(json.dumps({"type": "load_showfile", "filename": "example.json"}))
            await drain(pws)
            await drain(cws)

            await cws.send(json.dumps({"type": "pause", "paused": True}))
            state = await recv_type(cws, "full_state")
            self.assertTrue(state["paused"])

            await cws.send(json.dumps({"type": "go_armed"}))
            await drain(pws)
            state = await recv_type(cws, "full_state")
            self.assertEqual(state["current_cue_index"], 0)

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_unload_showfile(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await cws.send(json.dumps({"type": "load_showfile", "filename": "example.json"}))
            await drain(pws)
            await drain(cws)

            await cws.send(json.dumps({"type": "unload_showfile"}))
            await drain(pws)
            state = await recv_type(cws, "full_state")
            self.assertIsNone(state["showfile"])

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_missing_position_warning(self):
        async def run():
            cws, _, _ = await connect_caller()
            await cws.send(json.dumps({"type": "load_showfile", "filename": "example.json"}))
            state = await recv_type(cws, "full_state")
            self.assertIn("LX", state["missing_positions"])
            self.assertIn("SND", state["missing_positions"])
            await cws.close()
        asyncio.run(run())


class TestRenameAndRemove(CueLightTestCase):
    def test_rename_position(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await cws.send(json.dumps({"type": "rename", "client_id": "p1", "label": "LX OP"}))
            label_msg = await recv_type(pws, "label_changed")
            self.assertEqual(label_msg["label"], "LX OP")
            state = await recv_type(cws, "full_state")
            self.assertEqual(state["positions"]["p1"]["label"], "LX OP")

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_rename_duplicate_rejected(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws1, _ = await connect_position("p1", "LX")
            await drain(cws)
            pws2, _ = await connect_position("p2", "SND")
            await drain(cws)

            await cws.send(json.dumps({"type": "rename", "client_id": "p2", "label": "LX"}))
            await asyncio.sleep(0.3)
            msgs = await drain(pws2, timeout=0.3)
            types = [m.get("type") for m in msgs]
            self.assertNotIn("label_changed", types)

            await pws1.close()
            await pws2.close()
            await cws.close()
        asyncio.run(run())

    def test_remove_position(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await cws.send(json.dumps({"type": "remove_position", "client_id": "p1"}))
            msg = await recv_type(pws, "removed")
            self.assertEqual(msg["type"], "removed")
            state = await recv_type(cws, "full_state")
            self.assertNotIn("p1", state["positions"])

            await cws.close()
        asyncio.run(run())

    def test_removed_position_can_rejoin(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws1, _ = await connect_position("p1", "LX")
            await drain(cws)

            await cws.send(json.dumps({"type": "remove_position", "client_id": "p1"}))
            await recv_type(pws1, "removed")
            await drain(cws)

            pws2, msg = await connect_position("p1", "LX")
            self.assertEqual(msg["type"], "joined")

            await pws2.close()
            await cws.close()
        asyncio.run(run())


class TestPassword(CueLightTestCase):
    def test_set_and_check_password(self):
        async def run():
            cws, _, _ = await connect_caller()

            await cws.send(json.dumps({"type": "set_password", "enabled": True, "password": "secret"}))
            state = await recv_type(cws, "full_state")
            self.assertTrue(state["password_enabled"])

            req = urllib_request.Request(
                f"{HTTP_URL}/api/check_password",
                data=json.dumps({"password": "secret"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            resp = json.loads(urllib_request.urlopen(req).read())
            self.assertTrue(resp["ok"])

            req_bad = urllib_request.Request(
                f"{HTTP_URL}/api/check_password",
                data=json.dumps({"password": "wrong"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            resp_bad = json.loads(urllib_request.urlopen(req_bad).read())
            self.assertFalse(resp_bad["ok"])

            await cws.close()
        asyncio.run(run())


class TestCheckLabelAPI(CueLightTestCase):
    def test_label_available(self):
        async def run():
            cws, _, _ = await connect_caller()
            req = urllib_request.Request(
                f"{HTTP_URL}/api/check_label",
                data=json.dumps({"label": "UNIQUE", "client_id": "new"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            resp = json.loads(urllib_request.urlopen(req).read())
            self.assertTrue(resp["ok"])
            await cws.close()
        asyncio.run(run())

    def test_label_taken(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "TAKEN")
            await drain(cws)

            req = urllib_request.Request(
                f"{HTTP_URL}/api/check_label",
                data=json.dumps({"label": "TAKEN", "client_id": "other"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            resp = json.loads(urllib_request.urlopen(req).read())
            self.assertFalse(resp["ok"])

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_own_label_ok_on_reconnect(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "MINE")
            await drain(cws)

            req = urllib_request.Request(
                f"{HTTP_URL}/api/check_label",
                data=json.dumps({"label": "MINE", "client_id": "p1"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            resp = json.loads(urllib_request.urlopen(req).read())
            self.assertTrue(resp["ok"])

            await pws.close()
            await cws.close()
        asyncio.run(run())


class TestCallerDisconnect(CueLightTestCase):
    def test_positions_warned_on_caller_disconnect(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await cws.close()
            msg = await recv_type(pws, "caller_disconnected")
            self.assertEqual(msg["type"], "caller_disconnected")

            await pws.close()
        asyncio.run(run())


class TestExitShow(CueLightTestCase):
    def test_exit_notifies_positions(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await cws.send(json.dumps({"type": "exit"}))
            msg = await recv_type(pws, "show_ended")
            self.assertEqual(msg["type"], "show_ended")
        asyncio.run(run())


class TestHTTPEndpoints(CueLightTestCase):
    def test_api_info(self):
        resp = json.loads(urllib_request.urlopen(f"{HTTP_URL}/api/info").read())
        self.assertIn("ip", resp)
        self.assertIn("port", resp)

    def test_api_showfiles(self):
        resp = json.loads(urllib_request.urlopen(f"{HTTP_URL}/api/showfiles").read())
        self.assertIn("files", resp)
        self.assertIn("example.json", resp["files"])

    def test_api_get_showfile(self):
        resp = json.loads(urllib_request.urlopen(f"{HTTP_URL}/api/showfile/example.json").read())
        self.assertEqual(resp["show_name"], "Macbeth — Act I")
        self.assertGreater(len(resp["cues"]), 0)

    def test_api_qr_returns_png(self):
        resp = urllib_request.urlopen(f"{HTTP_URL}/api/qr")
        self.assertEqual(resp.headers.get_content_type(), "image/png")
        self.assertGreater(len(resp.read()), 100)

    def test_html_pages_served(self):
        for path in ["/", "/join", "/position", "/editor"]:
            resp = urllib_request.urlopen(f"{HTTP_URL}{path}")
            html = resp.read().decode()
            self.assertIn("<!DOCTYPE html>", html)


class TestShowfileValidation(CueLightTestCase):
    def test_valid_showfile_saved(self):
        data = json.dumps({
            "show_name": "Test",
            "cues": [{"sequence": 1, "scene": "1", "targets": [{"position": "LX", "cue_number": "1"}]}]
        }).encode()
        req = urllib_request.Request(
            f"{HTTP_URL}/api/showfile/_test_tmp.json",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        resp = json.loads(urllib_request.urlopen(req).read())
        self.assertTrue(resp["ok"])
        # Cleanup
        tmp = PROJECT_ROOT / "showfiles" / "_test_tmp.json"
        if tmp.exists():
            tmp.unlink()

    def test_invalid_showfile_rejected(self):
        data = json.dumps({"no_name": True}).encode()
        req = urllib_request.Request(
            f"{HTTP_URL}/api/showfile/_test_bad.json",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib_request.urlopen(req)
            self.fail("Should have returned 400")
        except urllib_request.HTTPError as e:
            self.assertEqual(e.code, 400)
            body = json.loads(e.read())
            self.assertIn("errors", body)


class TestOscPatchAPI(CueLightTestCase):
    def test_list_patches(self) -> None:
        resp = json.loads(urllib_request.urlopen(f"{HTTP_URL}/api/patches").read())
        self.assertIn("files", resp)
        self.assertIn("mainstage.json", resp["files"])

    def test_get_patch(self) -> None:
        resp = json.loads(urllib_request.urlopen(f"{HTTP_URL}/api/patch/mainstage.json").read())
        self.assertEqual(resp["name"], "Main Stage")
        self.assertGreater(len(resp["devices"]), 0)

    def test_save_and_validate_patch(self) -> None:
        data = json.dumps({
            "name": "Test Patch",
            "devices": [{"name": "TEST", "ip": "127.0.0.1", "port": 9000}]
        }).encode()
        req = urllib_request.Request(
            f"{HTTP_URL}/api/patch/_test_tmp.json",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        resp = json.loads(urllib_request.urlopen(req).read())
        self.assertTrue(resp["ok"])
        tmp = PROJECT_ROOT / "patches" / "_test_tmp.json"
        if tmp.exists():
            tmp.unlink()

    def test_invalid_patch_rejected(self) -> None:
        data = json.dumps({"no_name": True}).encode()
        req = urllib_request.Request(
            f"{HTTP_URL}/api/patch/_test_bad.json",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib_request.urlopen(req)
            self.fail("Should have returned 400")
        except urllib_request.HTTPError as e:
            self.assertEqual(e.code, 400)


class TestOscPositionInjection(CueLightTestCase):
    """Loading a patch injects OSC positions into the grid."""

    def _write_test_patch(self, devices: list[dict[str, object]]) -> Path:
        path = PROJECT_ROOT / "patches" / "_test_osc.json"
        path.write_text(json.dumps({"name": "Test", "devices": devices}))
        return path

    def _cleanup_patch(self) -> None:
        path = PROJECT_ROOT / "patches" / "_test_osc.json"
        if path.exists():
            path.unlink()

    def test_load_patch_creates_osc_positions(self) -> None:
        async def run() -> None:
            self._write_test_patch([
                {"name": "OSCSND", "ip": "127.0.0.1", "port": 9999, "expect_reply": False},
            ])
            try:
                cws, _, _ = await connect_caller()
                await cws.send(json.dumps({"type": "load_patch", "filename": "_test_osc.json"}))
                state = await recv_type(cws, "full_state")
                self.assertIn("osc:oscsnd", state["positions"])
                pos = state["positions"]["osc:oscsnd"]
                self.assertEqual(pos["type"], "osc")
                self.assertEqual(pos["label"], "OSCSND")
                self.assertTrue(pos["connected"])
                await cws.close()
            finally:
                self._cleanup_patch()
        asyncio.run(run())

    def test_unload_patch_removes_osc_positions(self) -> None:
        async def run() -> None:
            self._write_test_patch([
                {"name": "OSCLX", "ip": "127.0.0.1", "port": 9999, "expect_reply": False},
            ])
            try:
                cws, _, _ = await connect_caller()
                await cws.send(json.dumps({"type": "load_patch", "filename": "_test_osc.json"}))
                state = await recv_type(cws, "full_state")
                self.assertIn("osc:osclx", state["positions"])

                await cws.send(json.dumps({"type": "unload_patch"}))
                state = await recv_type(cws, "full_state")
                self.assertNotIn("osc:osclx", state["positions"])
                await cws.close()
            finally:
                self._cleanup_patch()
        asyncio.run(run())

    def test_osc_label_collision_with_human(self) -> None:
        async def run() -> None:
            self._write_test_patch([
                {"name": "LX", "ip": "127.0.0.1", "port": 9999, "expect_reply": False},
            ])
            try:
                cws, _, _ = await connect_caller()
                pws, _ = await connect_position("p1", "LX")
                await drain(cws)

                await cws.send(json.dumps({"type": "load_patch", "filename": "_test_osc.json"}))
                state = await recv_type(cws, "full_state")
                # The OSC device named LX should be skipped (collision)
                self.assertNotIn("osc:lx", state["positions"])
                self.assertIn("p1", state["positions"])

                await pws.close()
                await cws.close()
            finally:
                self._cleanup_patch()
        asyncio.run(run())

    def test_human_rejected_when_osc_label_exists(self) -> None:
        async def run() -> None:
            self._write_test_patch([
                {"name": "TAKEN", "ip": "127.0.0.1", "port": 9999, "expect_reply": False},
            ])
            try:
                cws, _, _ = await connect_caller()
                await cws.send(json.dumps({"type": "load_patch", "filename": "_test_osc.json"}))
                await drain(cws)

                pws, msg = await connect_position("p1", "TAKEN")
                try:
                    self.assertEqual(msg["type"], "join_rejected")
                    self.assertIn("already in use", msg["reason"])
                finally:
                    await pws.close()

                await cws.close()
            finally:
                self._cleanup_patch()
        asyncio.run(run())

    def test_osc_positions_armed_by_showfile(self) -> None:
        async def run() -> None:
            self._write_test_patch([
                {"name": "LX", "ip": "127.0.0.1", "port": 9999, "expect_reply": False},
                {"name": "SND", "ip": "127.0.0.1", "port": 9998, "expect_reply": False},
            ])
            try:
                cws, _, _ = await connect_caller()
                await cws.send(json.dumps({"type": "load_patch", "filename": "_test_osc.json"}))
                await drain(cws)

                await cws.send(json.dumps({"type": "load_showfile", "filename": "example.json"}))
                state = await recv_type(cws, "full_state")
                # First cue targets LX and SND
                self.assertTrue(state["positions"]["osc:lx"]["armed"])
                self.assertTrue(state["positions"]["osc:snd"]["armed"])

                await cws.close()
            finally:
                self._cleanup_patch()
        asyncio.run(run())


class TestOscFire(CueLightTestCase):
    """OSC fire sends the expected message to a device."""

    def _write_test_patch(self, devices: list[dict[str, object]]) -> Path:
        path = PROJECT_ROOT / "patches" / "_test_fire.json"
        path.write_text(json.dumps({"name": "Fire Test", "devices": devices}))
        return path

    def _cleanup_patch(self) -> None:
        path = PROJECT_ROOT / "patches" / "_test_fire.json"
        if path.exists():
            path.unlink()

    def test_go_fires_osc_and_reports_result(self) -> None:
        """GO on an OSC column fires OSC and the caller gets an osc_result message."""
        async def run() -> None:
            # Start a UDP listener to receive the OSC fire
            received = asyncio.Event()
            received_data = []

            class Listener(asyncio.DatagramProtocol):
                def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                    received_data.append(data)
                    received.set()

            loop = asyncio.get_event_loop()
            transport, _ = await loop.create_datagram_endpoint(
                Listener, local_addr=("127.0.0.1", 0)
            )
            listen_port = transport.get_extra_info("sockname")[1]

            self._write_test_patch([{
                "name": "TESTDEV",
                "ip": "127.0.0.1",
                "port": listen_port,
                "protocol": "udp",
                "go_template": "/test/go",
                "expect_reply": False,
            }])
            try:
                cws, _, _ = await connect_caller()
                await cws.send(json.dumps({"type": "load_patch", "filename": "_test_fire.json"}))
                await drain(cws)

                # Fire GO
                await cws.send(json.dumps({"type": "go", "client_id": "osc:testdev"}))
                osc_msg = await recv_type(cws, "osc_result")
                self.assertEqual(osc_msg["result"], "sent")
                self.assertEqual(osc_msg["client_id"], "osc:testdev")

                # Verify the OSC message was actually sent
                try:
                    await asyncio.wait_for(received.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                self.assertGreater(len(received_data), 0, "No OSC data received by listener")
                # The OSC message should contain /test/go
                self.assertIn(b"/test/go", received_data[0])

                await cws.close()
            finally:
                transport.close()
                self._cleanup_patch()
        asyncio.run(run())

    def test_go_with_cue_template_substitution(self) -> None:
        """GO template {cue} is substituted with the showfile cue number."""
        async def run() -> None:
            received_data: list[bytes] = []

            class Listener(asyncio.DatagramProtocol):
                def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                    received_data.append(data)

            loop = asyncio.get_event_loop()
            transport, _ = await loop.create_datagram_endpoint(
                Listener, local_addr=("127.0.0.1", 0)
            )
            listen_port = transport.get_extra_info("sockname")[1]

            self._write_test_patch([{
                "name": "LX",
                "ip": "127.0.0.1",
                "port": listen_port,
                "protocol": "udp",
                "go_template": "/cue/{cue}/start",
                "expect_reply": False,
            }])
            try:
                cws, _, _ = await connect_caller()
                await cws.send(json.dumps({"type": "load_patch", "filename": "_test_fire.json"}))
                await drain(cws)

                await cws.send(json.dumps({"type": "load_showfile", "filename": "example.json"}))
                await drain(cws)

                # GO on the armed OSC position — cue 1 targets LX with cue_number "1"
                await cws.send(json.dumps({"type": "go", "client_id": "osc:lx"}))
                await recv_type(cws, "osc_result")
                await asyncio.sleep(0.2)

                self.assertGreater(len(received_data), 0)
                self.assertIn(b"/cue/1/start", received_data[0])

                await cws.close()
            finally:
                transport.close()
                self._cleanup_patch()
        asyncio.run(run())

    def test_master_go_fires_osc_positions(self) -> None:
        """Master GO fires armed OSC positions and still advances the cue."""
        async def run() -> None:
            received: list[bytes] = []

            class Listener(asyncio.DatagramProtocol):
                def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                    received.append(data)

            loop = asyncio.get_event_loop()
            transport, _ = await loop.create_datagram_endpoint(
                Listener, local_addr=("127.0.0.1", 0)
            )
            listen_port = transport.get_extra_info("sockname")[1]

            self._write_test_patch([{
                "name": "LX",
                "ip": "127.0.0.1",
                "port": listen_port,
                "protocol": "udp",
                "go_template": "/go",
                "expect_reply": False,
            }])
            try:
                cws, _, _ = await connect_caller()
                await cws.send(json.dumps({"type": "load_patch", "filename": "_test_fire.json"}))
                await drain(cws)

                await cws.send(json.dumps({"type": "load_showfile", "filename": "example.json"}))
                await drain(cws)

                # Master GO
                await cws.send(json.dumps({"type": "go_armed"}))
                osc_msg = await recv_type(cws, "osc_result")
                self.assertEqual(osc_msg["result"], "sent")

                state = await recv_type(cws, "full_state")
                self.assertEqual(state["current_cue_index"], 1)

                await asyncio.sleep(0.2)
                self.assertGreater(len(received), 0)

                await cws.close()
            finally:
                transport.close()
                self._cleanup_patch()
        asyncio.run(run())


class TestOscProbe(CueLightTestCase):
    """Probe tiers return the expected states."""

    def _write_test_patch(self, devices: list[dict[str, object]]) -> Path:
        path = PROJECT_ROOT / "patches" / "_test_probe.json"
        path.write_text(json.dumps({"name": "Probe Test", "devices": devices}))
        return path

    def _cleanup_patch(self) -> None:
        path = PROJECT_ROOT / "patches" / "_test_probe.json"
        if path.exists():
            path.unlink()

    def test_udp_no_ping_returns_unverified(self) -> None:
        """UDP-only device with no ping_template → UNVERIFIED."""
        async def run() -> None:
            self._write_test_patch([{
                "name": "UDPDEV",
                "ip": "127.0.0.1",
                "port": 59999,
                "protocol": "udp",
                "go_template": "/go",
                "ping_template": "",
                "expect_reply": False,
            }])
            try:
                cws, _, _ = await connect_caller()
                await cws.send(json.dumps({"type": "load_patch", "filename": "_test_probe.json"}))
                await drain(cws)

                await cws.send(json.dumps({"type": "standby", "client_id": "osc:udpdev"}))
                state = await recv_type(cws, "full_state")
                pos = state["positions"]["osc:udpdev"]
                self.assertEqual(pos["osc_probe"], "unverified")
                self.assertEqual(pos["osc_trust"], "none")

                await cws.close()
            finally:
                self._cleanup_patch()
        asyncio.run(run())

    def test_tcp_port_open_returns_confirmed(self) -> None:
        """TCP connect to an open port → CONFIRMED/tcp_port."""
        async def run() -> None:
            import socket as sock_mod
            server_sock = sock_mod.socket(sock_mod.AF_INET, sock_mod.SOCK_STREAM)
            server_sock.setsockopt(sock_mod.SOL_SOCKET, sock_mod.SO_REUSEADDR, 1)
            server_sock.bind(("127.0.0.1", 0))
            server_sock.listen(1)
            tcp_port = server_sock.getsockname()[1]

            self._write_test_patch([{
                "name": "TCPDEV",
                "ip": "127.0.0.1",
                "port": tcp_port,
                "protocol": "tcp",
                "go_template": "/go",
                "ping_template": "",
                "expect_reply": False,
            }])
            try:
                cws, _, _ = await connect_caller()
                await cws.send(json.dumps({"type": "load_patch", "filename": "_test_probe.json"}))
                await drain(cws)

                await cws.send(json.dumps({"type": "standby", "client_id": "osc:tcpdev"}))
                state = await recv_type(cws, "full_state")
                pos = state["positions"]["osc:tcpdev"]
                self.assertEqual(pos["osc_probe"], "confirmed")
                self.assertEqual(pos["osc_trust"], "tcp_port")

                await cws.close()
            finally:
                server_sock.close()
                self._cleanup_patch()
        asyncio.run(run())

    def test_tcp_port_closed_returns_failed(self) -> None:
        """TCP connect to a closed port → FAILED/tcp_port."""
        async def run() -> None:
            self._write_test_patch([{
                "name": "DEADDEV",
                "ip": "127.0.0.1",
                "port": 59998,
                "protocol": "tcp",
                "go_template": "/go",
                "ping_template": "",
                "expect_reply": False,
            }])
            try:
                cws, _, _ = await connect_caller()
                await cws.send(json.dumps({"type": "load_patch", "filename": "_test_probe.json"}))
                await drain(cws)

                await cws.send(json.dumps({"type": "standby", "client_id": "osc:deaddev"}))
                state = await recv_type(cws, "full_state")
                pos = state["positions"]["osc:deaddev"]
                self.assertEqual(pos["osc_probe"], "failed")
                self.assertEqual(pos["osc_trust"], "tcp_port")

                await cws.close()
            finally:
                self._cleanup_patch()
        asyncio.run(run())

    def test_osc_reply_probe_confirmed(self) -> None:
        """UDP device with ping_template that receives a reply → CONFIRMED/osc_reply."""
        async def run() -> None:
            class EchoProtocol(asyncio.DatagramProtocol):
                def __init__(self) -> None:
                    self.transport: asyncio.DatagramTransport | None = None
                def connection_made(self, transport: asyncio.BaseTransport) -> None:
                    self.transport = transport  # type: ignore[assignment]
                def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                    if self.transport:
                        self.transport.sendto(data, addr)

            loop = asyncio.get_event_loop()
            transport, _ = await loop.create_datagram_endpoint(
                EchoProtocol, local_addr=("127.0.0.1", 0)
            )
            echo_port = transport.get_extra_info("sockname")[1]

            self._write_test_patch([{
                "name": "ECHODEV",
                "ip": "127.0.0.1",
                "port": echo_port,
                "protocol": "udp",
                "go_template": "/go",
                "ping_template": "/ping",
                "expect_reply": True,
            }])
            try:
                cws, _, _ = await connect_caller()
                await cws.send(json.dumps({"type": "load_patch", "filename": "_test_probe.json"}))
                await drain(cws)

                await cws.send(json.dumps({"type": "standby", "client_id": "osc:echodev"}))
                state = await recv_type(cws, "full_state")
                pos = state["positions"]["osc:echodev"]
                self.assertEqual(pos["osc_probe"], "confirmed")
                self.assertEqual(pos["osc_trust"], "osc_reply")

                await cws.close()
            finally:
                transport.close()
                self._cleanup_patch()
        asyncio.run(run())

    def test_fire_no_reply_timeout(self) -> None:
        """Fire with expect_reply=True to a silent listener → NO_REPLY."""
        async def run() -> None:
            class SilentProtocol(asyncio.DatagramProtocol):
                def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                    pass

            loop = asyncio.get_event_loop()
            transport, _ = await loop.create_datagram_endpoint(
                SilentProtocol, local_addr=("127.0.0.1", 0)
            )
            silent_port = transport.get_extra_info("sockname")[1]

            self._write_test_patch([{
                "name": "SILENTDEV",
                "ip": "127.0.0.1",
                "port": silent_port,
                "protocol": "udp",
                "go_template": "/go",
                "expect_reply": True,
            }])
            try:
                cws, _, _ = await connect_caller()
                await cws.send(json.dumps({"type": "load_patch", "filename": "_test_probe.json"}))
                await drain(cws)

                await cws.send(json.dumps({"type": "go", "client_id": "osc:silentdev"}))
                osc_msg = await recv_type(cws, "osc_result")
                self.assertEqual(osc_msg["result"], "no_reply")

                await cws.close()
            finally:
                transport.close()
                self._cleanup_patch()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
