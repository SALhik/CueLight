"""
Integration tests for CueLight.

Spins up a real uvicorn server on port 8001 and tests via WebSocket connections.
Run with: python3 tests/test_cuelight.py
    or:   python3 -m pytest tests/test_cuelight.py -v
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib import request as urllib_request
from urllib.error import HTTPError

import uvicorn
from websockets.asyncio.client import connect
import websockets.exceptions

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_SNAPSHOT = PROJECT_ROOT / "state" / "snapshot.json"
TEST_PORT = 8001
WS_URL = f"ws://localhost:{TEST_PORT}"
HTTP_URL = f"http://localhost:{TEST_PORT}"


def _clean_state():
    state_dir = PROJECT_ROOT / "state"
    for name in ("snapshot.json", "snapshot.bak", "showlog.jsonl", "showlog.bak"):
        p = state_dir / name
        if p.exists():
            p.unlink()


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


async def recv_settled_probe(cws, client_id: str, timeout: float = 3.0):
    """Waits for a full_state where client_id's probe is no longer 'probing'.
    STANDBY on an OSC column pushes an interim 'probing' state first."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = await asyncio.wait_for(cws.recv(), timeout=deadline - time.time())
        msg = json.loads(raw)
        if msg.get("type") == "full_state":
            pos = msg["positions"].get(client_id)
            if pos and pos["osc_probe"] != "probing":
                return pos
    raise AssertionError(f"probe for {client_id} did not settle within {timeout}s")


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
    def test_non_ascii_password(self):
        """Passwords aren't restricted to ASCII."""
        async def run():
            cws, _, _ = await connect_caller()
            await cws.send(json.dumps({"type": "set_password", "enabled": True, "password": "pässwörd✓"}))
            await recv_type(cws, "full_state")

            req = urllib_request.Request(
                f"{HTTP_URL}/api/check_password",
                data=json.dumps({"password": "pässwörd✓"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            resp = json.loads(urllib_request.urlopen(req).read())
            self.assertTrue(resp["ok"])

            await cws.close()
        asyncio.run(run())

    def test_non_string_password_attempt_rejected(self):
        """A non-string JSON value must be rejected, not crash the endpoint."""
        async def run():
            cws, _, _ = await connect_caller()
            await cws.send(json.dumps({"type": "set_password", "enabled": True, "password": "123"}))
            await recv_type(cws, "full_state")

            req = urllib_request.Request(
                f"{HTTP_URL}/api/check_password",
                data=json.dumps({"password": 123}).encode(),
                headers={"Content-Type": "application/json"},
            )
            resp = json.loads(urllib_request.urlopen(req).read())
            self.assertFalse(resp["ok"])

            await cws.close()
        asyncio.run(run())

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
        # Must reflect the port actually being served, not a hardcoded 8000
        self.assertEqual(resp["port"], TEST_PORT)
        # mDNS hostname when registration succeeded, "" otherwise — always present
        self.assertIn("mdns_host", resp)
        self.assertIsInstance(resp["mdns_host"], str)

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
        first = resp["devices"][0]
        self.assertIn("protocol", first)
        self.assertIn("expect_reply", first)
        self.assertIn("preset", first)

    def test_save_and_validate_patch(self) -> None:
        tmp = PROJECT_ROOT / "patches" / "_test_tmp.json"
        try:
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
        finally:
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
            received = asyncio.Event()
            received_data: list[bytes] = []

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
                await asyncio.wait_for(received.wait(), timeout=1.0)

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
            received_event = asyncio.Event()
            received: list[bytes] = []

            class Listener(asyncio.DatagramProtocol):
                def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                    received.append(data)
                    received_event.set()

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

                await asyncio.wait_for(received_event.wait(), timeout=1.0)
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
                pos = await recv_settled_probe(cws, "osc:udpdev")
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
                pos = await recv_settled_probe(cws, "osc:tcpdev")
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
                pos = await recv_settled_probe(cws, "osc:deaddev")
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
                pos = await recv_settled_probe(cws, "osc:echodev")
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


class TestOscNonBlocking(CueLightTestCase):
    """OSC I/O must not delay human positions or block the caller."""

    PATCH_FILE = "_test_slow.json"

    def _write_test_patch(self, devices: list[dict[str, object]]) -> None:
        path = PROJECT_ROOT / "patches" / self.PATCH_FILE
        path.write_text(json.dumps({"name": "Slow Test", "devices": devices}))

    def _cleanup_patch(self) -> None:
        path = PROJECT_ROOT / "patches" / self.PATCH_FILE
        if path.exists():
            path.unlink()

    class _SilentProtocol(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            pass

    async def _silent_listener(self):
        loop = asyncio.get_event_loop()
        transport, _ = await loop.create_datagram_endpoint(
            self._SilentProtocol, local_addr=("127.0.0.1", 0)
        )
        return transport, transport.get_extra_info("sockname")[1]

    def test_master_go_humans_not_delayed_by_slow_osc(self):
        """Two OSC devices that each take the full 400ms fire timeout must not
        delay the go_called message to a human position."""
        async def run():
            t1, port1 = await self._silent_listener()
            t2, port2 = await self._silent_listener()
            self._write_test_patch([
                {"name": "SLOW1", "ip": "127.0.0.1", "port": port1, "protocol": "udp",
                 "go_template": "/go", "expect_reply": True},
                {"name": "SLOW2", "ip": "127.0.0.1", "port": port2, "protocol": "udp",
                 "go_template": "/go", "expect_reply": True},
            ])
            try:
                cws, _, _ = await connect_caller()
                # Patch first so the OSC positions precede the human in dict order
                await cws.send(json.dumps({"type": "load_patch", "filename": self.PATCH_FILE}))
                await drain(cws)
                pws, _ = await connect_position("p1", "LX")
                await drain(cws)

                for cid in ("osc:slow1", "osc:slow2", "p1"):
                    await cws.send(json.dumps({"type": "toggle_arm", "client_id": cid}))
                await drain(cws)

                t0 = time.time()
                await cws.send(json.dumps({"type": "go_armed"}))
                await recv_type(pws, "go_called", timeout=3.0)
                elapsed = time.time() - t0
                self.assertLess(elapsed, 0.5, f"human GO delayed {elapsed:.2f}s by OSC fires")

                # Both fires still complete and report their results
                results = {}
                deadline = time.time() + 3.0
                while len(results) < 2 and time.time() < deadline:
                    raw = await asyncio.wait_for(cws.recv(), timeout=deadline - time.time())
                    m = json.loads(raw)
                    if m.get("type") == "osc_result":
                        results[m["client_id"]] = m["result"]
                self.assertEqual(set(results), {"osc:slow1", "osc:slow2"})

                await pws.close()
                await cws.close()
            finally:
                t1.close()
                t2.close()
                self._cleanup_patch()
        asyncio.run(run())

    def test_caller_not_blocked_during_osc_fire(self):
        """While an OSC fire is waiting on its reply timeout, the caller's next
        action must still go through immediately."""
        async def run():
            transport, port = await self._silent_listener()
            self._write_test_patch([
                {"name": "SLOW1", "ip": "127.0.0.1", "port": port, "protocol": "udp",
                 "go_template": "/go", "expect_reply": True},
            ])
            try:
                cws, _, _ = await connect_caller()
                await cws.send(json.dumps({"type": "load_patch", "filename": self.PATCH_FILE}))
                await drain(cws)
                pws, _ = await connect_position("p1", "LX")
                await drain(cws)

                t0 = time.time()
                await cws.send(json.dumps({"type": "go", "client_id": "osc:slow1"}))
                await cws.send(json.dumps({"type": "standby", "client_id": "p1"}))
                await recv_type(pws, "standby_called", timeout=3.0)
                elapsed = time.time() - t0
                self.assertLess(elapsed, 0.3, f"standby delayed {elapsed:.2f}s by OSC fire")

                await pws.close()
                await cws.close()
            finally:
                transport.close()
                self._cleanup_patch()
        asyncio.run(run())


class TestOscHeartbeatDetection(CueLightTestCase):
    """The ~5s heartbeat notices a confirmable device going away."""

    PATCH_FILE = "_test_hb.json"

    def _cleanup_patch(self) -> None:
        path = PROJECT_ROOT / "patches" / self.PATCH_FILE
        if path.exists():
            path.unlink()

    def test_heartbeat_detects_dead_device(self):
        async def run():
            import socket as sock_mod
            server_sock = sock_mod.socket(sock_mod.AF_INET, sock_mod.SOCK_STREAM)
            server_sock.setsockopt(sock_mod.SOL_SOCKET, sock_mod.SO_REUSEADDR, 1)
            server_sock.bind(("127.0.0.1", 0))
            server_sock.listen(1)
            tcp_port = server_sock.getsockname()[1]

            path = PROJECT_ROOT / "patches" / self.PATCH_FILE
            path.write_text(json.dumps({"name": "HB Test", "devices": [{
                "name": "HBDEV", "ip": "127.0.0.1", "port": tcp_port,
                "protocol": "tcp", "go_template": "/go", "ping_template": "",
                "expect_reply": False,
            }]}))
            try:
                cws, _, _ = await connect_caller()
                await cws.send(json.dumps({"type": "load_patch", "filename": self.PATCH_FILE}))
                await drain(cws)

                # Establish the trust tier so the heartbeat picks the device up
                await cws.send(json.dumps({"type": "standby", "client_id": "osc:hbdev"}))
                deadline = time.time() + 3.0
                confirmed = False
                while time.time() < deadline and not confirmed:
                    raw = await asyncio.wait_for(cws.recv(), timeout=deadline - time.time())
                    m = json.loads(raw)
                    if m.get("type") == "full_state":
                        pos = m["positions"].get("osc:hbdev", {})
                        if pos.get("osc_probe") == "confirmed":
                            confirmed = True
                self.assertTrue(confirmed)

                # Kill the device; the heartbeat should flag it within ~2 cycles
                server_sock.close()
                deadline = time.time() + 12.0
                failed = False
                while time.time() < deadline and not failed:
                    try:
                        raw = await asyncio.wait_for(cws.recv(), timeout=deadline - time.time())
                    except asyncio.TimeoutError:
                        break
                    m = json.loads(raw)
                    if m.get("type") == "full_state":
                        pos = m["positions"].get("osc:hbdev", {})
                        if pos.get("osc_probe") == "failed":
                            failed = True
                self.assertTrue(failed, "heartbeat never flagged the dead device")

                await cws.close()
            finally:
                server_sock.close()
                self._cleanup_patch()
        asyncio.run(run())


class TestPersistenceSnapshot(CueLightTestCase):
    """save_state must freeze state at call time; wipe_state must cancel pending writes."""

    def setUp(self):
        super().setUp()
        import server.persistence as persistence
        self.persistence = persistence
        self._orig_dir = persistence.STATE_DIR
        self._orig_path = persistence.SNAPSHOT_PATH
        self._orig_bak = persistence.BACKUP_PATH
        tmpdir = Path(tempfile.mkdtemp())
        persistence.STATE_DIR = tmpdir
        persistence.SNAPSHOT_PATH = tmpdir / "snapshot.json"
        persistence.BACKUP_PATH = tmpdir / "snapshot.bak"

    def tearDown(self):
        self.persistence.STATE_DIR = self._orig_dir
        self.persistence.SNAPSHOT_PATH = self._orig_path
        self.persistence.BACKUP_PATH = self._orig_bak

    def test_wipe_cancels_pending_write(self):
        from server.models import AppState
        self.persistence.save_state(AppState())
        self.persistence.wipe_state()
        time.sleep(0.3)
        self.assertFalse(
            self.persistence.SNAPSHOT_PATH.exists(),
            "pending debounced write resurrected the snapshot after wipe_state()",
        )

    def test_save_freezes_state_at_call_time(self):
        from server.models import AppState, Position
        state = AppState()
        state.positions["p1"] = Position(client_id="p1", label="LX")
        self.persistence.save_state(state)
        # Mutation after save_state but before the debounce fires must not leak
        # into the written snapshot.
        state.positions["p2"] = Position(client_id="p2", label="SND")
        time.sleep(0.3)
        data = json.loads(self.persistence.SNAPSHOT_PATH.read_text())
        self.assertIn("p1", data["positions"])
        self.assertNotIn("p2", data["positions"])


class TestShowfileRestore(CueLightTestCase):
    """The showfile is restored by filename on startup, keeping the cue index."""

    def setUp(self):
        super().setUp()
        import server.persistence as persistence
        self.persistence = persistence
        self._orig_dir = persistence.STATE_DIR
        self._orig_path = persistence.SNAPSHOT_PATH
        tmpdir = Path(tempfile.mkdtemp())
        persistence.STATE_DIR = tmpdir
        persistence.SNAPSHOT_PATH = tmpdir / "snapshot.json"

    def tearDown(self):
        self.persistence.STATE_DIR = self._orig_dir
        self.persistence.SNAPSHOT_PATH = self._orig_path

    def test_snapshot_round_trips_showfile_filename_and_index(self):
        from server.models import AppState
        from server.showfile import load_showfile as load_sf
        state = AppState()
        state.showfile = load_sf("example.json")
        state.showfile_filename = "example.json"
        state.current_cue_index = 2
        self.persistence.save_state(state)
        time.sleep(0.3)
        loaded = self.persistence.load_state()
        self.assertEqual(loaded.showfile_filename, "example.json")
        self.assertEqual(loaded.current_cue_index, 2)
        # The full showfile is not stored — only the filename.
        self.assertIsNone(loaded.showfile)
        data = json.loads(self.persistence.SNAPSHOT_PATH.read_text())
        self.assertNotIn("showfile", data)

    def test_restore_showfile_keeps_cue_index(self):
        async def run():
            from server.models import AppState
            from server.showfile import load_showfile as load_sf
            from server.state import StateManager
            state = AppState()
            state.current_cue_index = 2
            state.showfile_filename = "example.json"
            mgr = StateManager(state)
            await mgr.restore_showfile(load_sf("example.json"))
            self.assertIsNotNone(mgr.state.showfile)
            self.assertEqual(mgr.state.current_cue_index, 2)
        asyncio.run(run())

    def test_restore_showfile_clamps_out_of_range_index(self):
        async def run():
            from server.models import AppState
            from server.showfile import load_showfile as load_sf
            from server.state import StateManager
            state = AppState()
            state.current_cue_index = 999
            state.showfile_filename = "example.json"
            mgr = StateManager(state)
            sf = load_sf("example.json")
            await mgr.restore_showfile(sf)
            self.assertEqual(mgr.state.current_cue_index, len(sf.cues) - 1)
        asyncio.run(run())

    def test_load_showfile_persists_filename(self):
        async def run():
            cws, _, _ = await connect_caller()
            await cws.send(json.dumps({"type": "load_showfile", "filename": "example.json"}))
            await recv_type(cws, "full_state")
            await asyncio.sleep(0.3)
            data = json.loads(self.persistence.SNAPSHOT_PATH.read_text())
            self.assertEqual(data["showfile_filename"], "example.json")
            await cws.close()
        asyncio.run(run())


class TestFilenameSanitization(CueLightTestCase):
    BAD_NAMES = ("../evil.json", "/etc/evil.json", "sub/evil.json", "evil.txt", ".json")

    def tearDown(self):
        # If sanitization is missing, the save calls below may create files —
        # remove them so a failing run doesn't pollute the repo.
        for p in (
            PROJECT_ROOT / "evil.json",
            PROJECT_ROOT / "showfiles" / "evil.txt",
            PROJECT_ROOT / "showfiles" / ".json",
            PROJECT_ROOT / "patches" / "evil.txt",
            PROJECT_ROOT / "patches" / ".json",
        ):
            if p.exists():
                p.unlink()

    def test_showfile_functions_reject_unsafe_names(self):
        from server.showfile import load_showfile as load_sf
        from server.showfile import save_showfile as save_sf
        for bad in self.BAD_NAMES:
            with self.assertRaises(ValueError, msg=f"load_showfile accepted {bad!r}"):
                load_sf(bad)
            with self.assertRaises(ValueError, msg=f"save_showfile accepted {bad!r}"):
                save_sf(bad, {"show_name": "x", "cues": []})

    def test_patch_functions_reject_unsafe_names(self):
        from server.patch import load_patch as load_p
        from server.patch import save_patch as save_p
        for bad in self.BAD_NAMES:
            with self.assertRaises(ValueError, msg=f"load_patch accepted {bad!r}"):
                load_p(bad)
            with self.assertRaises(ValueError, msg=f"save_patch accepted {bad!r}"):
                save_p(bad, {"name": "x", "devices": []})

    def test_http_save_rejects_unsafe_filename(self):
        for url in (f"{HTTP_URL}/api/showfile/evil.txt", f"{HTTP_URL}/api/patch/evil.txt"):
            body = {"show_name": "x", "cues": []} if "showfile" in url else {"name": "x", "devices": []}
            req = urllib_request.Request(
                url,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            try:
                urllib_request.urlopen(req)
                self.fail(f"{url} accepted a non-.json filename")
            except urllib_request.HTTPError as e:
                self.assertEqual(e.code, 400)


class TestMalformedMessages(CueLightTestCase):
    def test_caller_survives_malformed_message(self):
        """A message missing a required field must not kill the caller connection."""
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await cws.send(json.dumps({"type": "standby"}))  # no client_id
            await asyncio.sleep(0.2)

            await cws.send(json.dumps({"type": "standby", "client_id": "p1"}))
            await recv_type(pws, "standby_called")

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_position_survives_malformed_message(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await pws.send(json.dumps({"type": "rename"}))  # no label
            await asyncio.sleep(0.2)

            # The position connection must still be registered and receiving
            await cws.send(json.dumps({"type": "standby", "client_id": "p1"}))
            await recv_type(pws, "standby_called")

            await pws.close()
            await cws.close()
        asyncio.run(run())


class TestCallerTakeover(CueLightTestCase):
    def test_same_id_takes_over_stale_caller(self):
        """A reconnect with the same caller client_id supersedes a stale socket
        (e.g. the iPad slept and the old connection is half-open)."""
        async def run():
            cws1, _, _ = await connect_caller("caller-1")
            pws, _ = await connect_position("p1", "LX")
            await drain(cws1)

            cws2 = await connect(f"{WS_URL}/ws/caller")
            await cws2.send(json.dumps({"client_id": "caller-1"}))
            role = json.loads(await cws2.recv())
            self.assertEqual(role["type"], "role_assigned")
            state = json.loads(await cws2.recv())
            self.assertEqual(state["type"], "full_state")

            # The new socket drives the show
            await cws2.send(json.dumps({"type": "standby", "client_id": "p1"}))
            await recv_type(pws, "standby_called")

            # The stale socket closing must not tell positions the caller left
            await asyncio.sleep(0.3)
            msgs = await drain(pws, timeout=0.3)
            types = [m.get("type") for m in msgs]
            self.assertNotIn("caller_disconnected", types)

            await pws.close()
            await cws2.close()
        asyncio.run(run())

    def test_different_id_still_rejected(self):
        async def run():
            cws1, _, _ = await connect_caller("caller-1")
            cws2 = await connect(f"{WS_URL}/ws/caller")
            await cws2.send(json.dumps({"client_id": "caller-other"}))
            msg = json.loads(await cws2.recv())
            self.assertEqual(msg["type"], "role_rejected")
            await cws2.close()
            await cws1.close()
        asyncio.run(run())


class TestHealthPongReset(CueLightTestCase):
    def test_responsive_position_never_marked_red(self):
        """A position that answers every ping must never be marked red, even
        past the missed-pong limit window."""
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            async def pong_pings():
                while True:
                    raw = await pws.recv()
                    m = json.loads(raw)
                    if m.get("type") == "ping":
                        await pws.send(json.dumps({"type": "pong", "ts": m["ts"]}))

            ponger = asyncio.create_task(pong_pings())
            deadline = time.time() + 4.5
            saw_red = False
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(cws.recv(), timeout=deadline - time.time())
                except asyncio.TimeoutError:
                    break
                m = json.loads(raw)
                if m.get("type") == "full_state" and m["positions"].get("p1", {}).get("health") == "red":
                    saw_red = True
            ponger.cancel()
            self.assertFalse(saw_red, "healthy, ponging position was marked red")

            await pws.close()
            await cws.close()
        asyncio.run(run())


class TestHealthNotifications(CueLightTestCase):
    def test_caller_notified_when_position_goes_unhealthy(self):
        """A position that stops answering pings must push a red full_state to
        the caller without any other mutation happening."""
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            # This test position never answers pings, so after the missed-pong
            # limit the server should notify the caller on its own.
            deadline = time.time() + 6.0
            saw_red = False
            while time.time() < deadline and not saw_red:
                try:
                    raw = await asyncio.wait_for(cws.recv(), timeout=deadline - time.time())
                except asyncio.TimeoutError:
                    break
                m = json.loads(raw)
                if m.get("type") == "full_state" and m["positions"].get("p1", {}).get("health") == "red":
                    saw_red = True
            self.assertTrue(saw_red, "caller was not notified when position health went red")

            await pws.close()
            await cws.close()
        asyncio.run(run())


class TestShowfileCsv(CueLightTestCase):
    """CSV import/export for showfiles. Columns: sequence,scene,targets,note.
    Targets are ;-separated POSITION:CUE pairs."""

    def _post(self, path: str, body: bytes, content_type: str = "text/csv"):
        req = urllib_request.Request(
            f"{HTTP_URL}{path}",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        try:
            resp = urllib_request.urlopen(req, timeout=2)
            return resp.status, resp.read()
        except HTTPError as e:
            return e.code, e.read()

    def test_csv_import(self):
        csv_text = (
            "sequence,scene,targets,note\n"
            '1,1.1,LX:1;SND:1,"Blackout, thunder"\n'
            "2,1.2,Fly 1:12a,\n"
        )
        status, body = self._post("/api/csv/import", csv_text.encode())
        self.assertEqual(status, 200)
        cues = json.loads(body)["cues"]
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["sequence"], 1)
        self.assertEqual(cues[0]["scene"], "1.1")
        self.assertEqual(cues[0]["targets"], [
            {"position": "LX", "cue_number": "1"},
            {"position": "SND", "cue_number": "1"},
        ])
        self.assertEqual(cues[0]["note"], "Blackout, thunder")
        self.assertEqual(cues[1]["targets"], [{"position": "Fly 1", "cue_number": "12a"}])
        self.assertEqual(cues[1]["note"], "")

    def test_csv_import_bom_and_column_order(self):
        # Excel exports UTF-8 CSVs with a BOM; column order must not matter
        csv_text = "﻿scene,sequence,targets\n1.1,1,LX:1\n"
        status, body = self._post("/api/csv/import", csv_text.encode("utf-8"))
        self.assertEqual(status, 200)
        cues = json.loads(body)["cues"]
        self.assertEqual(cues[0]["sequence"], 1)
        self.assertEqual(cues[0]["note"], "")

    def test_csv_import_errors(self):
        status, body = self._post("/api/csv/import", b"foo,bar\n1,2\n")
        self.assertEqual(status, 400)
        self.assertTrue(any("sequence" in e for e in json.loads(body)["errors"]))

        status, body = self._post(
            "/api/csv/import", b"sequence,scene,targets\nX,1.1,LX:1\n")
        self.assertEqual(status, 400)
        self.assertTrue(any("Row 2" in e for e in json.loads(body)["errors"]))

        status, body = self._post(
            "/api/csv/import", b"sequence,scene,targets\n1,1.1,LX\n")
        self.assertEqual(status, 400)
        self.assertTrue(any("POSITION:CUE" in e for e in json.loads(body)["errors"]))

    def test_csv_export_round_trip(self):
        showfile = {"show_name": "T", "version": 1, "cues": [
            {
                "sequence": 1,
                "scene": "1.1",
                "targets": [
                    {"position": "LX", "cue_number": "1"},
                    {"position": "Fly 1", "cue_number": "12a"},
                ],
                "note": 'Note, with "quotes"',
            },
            {"sequence": 2, "scene": "1.2", "targets": [], "note": ""},
        ]}
        status, body = self._post(
            "/api/csv/export", json.dumps(showfile).encode(), "application/json")
        self.assertEqual(status, 200)
        status, body = self._post("/api/csv/import", body)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["cues"], showfile["cues"])


async def connect_observer(client_id: str = "obs1", password: str = ""):
    ws = await connect(f"{WS_URL}/ws/observer")
    await ws.send(json.dumps({"client_id": client_id, "password": password}))
    role = json.loads(await ws.recv())
    return ws, role


class TestObserver(CueLightTestCase):
    """Read-only observer role: mirrors the caller's full_state, sends nothing,
    and can only take over as caller once the caller is gone."""

    def test_observer_receives_state_updates(self):
        async def run():
            cws, _, _ = await connect_caller()
            ows, role = await connect_observer()
            self.assertEqual(role["type"], "role_assigned")
            self.assertEqual(role["role"], "observer")
            state = json.loads(await ows.recv())
            self.assertEqual(state["type"], "full_state")
            self.assertTrue(state["caller_connected"])

            pws, _ = await connect_position("p1", "LX")
            state = await recv_type(ows, "full_state")
            self.assertIn("p1", state["positions"])

            await pws.close()
            await ows.close()
            await cws.close()
        asyncio.run(run())

    def test_observer_password_redacted(self):
        async def run():
            cws, _, _ = await connect_caller()
            await cws.send(json.dumps({"type": "set_password", "enabled": True, "password": "secret"}))
            await recv_type(cws, "full_state")

            ows, role = await connect_observer(password="secret")
            self.assertEqual(role["type"], "role_assigned")
            state = json.loads(await ows.recv())
            self.assertEqual(state["password"], "")
            self.assertTrue(state["password_enabled"])

            # Updates stay redacted for observers but not for the caller
            pws, _ = await connect_position("p1", "LX")
            cstate = await recv_type(cws, "full_state")
            self.assertEqual(cstate["password"], "secret")
            ostate = await recv_type(ows, "full_state")
            self.assertEqual(ostate["password"], "")

            await pws.close()
            await ows.close()
            await cws.close()
        asyncio.run(run())

    def test_observer_wrong_password_rejected(self):
        async def run():
            cws, _, _ = await connect_caller()
            await cws.send(json.dumps({"type": "set_password", "enabled": True, "password": "secret"}))
            await recv_type(cws, "full_state")

            ows, msg = await connect_observer(password="wrong")
            self.assertEqual(msg["type"], "role_rejected")

            await ows.close()
            await cws.close()
        asyncio.run(run())

    def test_observer_messages_ignored(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)
            ows, _ = await connect_observer()
            json.loads(await ows.recv())  # initial full_state

            await ows.send(json.dumps({"type": "standby", "client_id": "p1"}))
            msgs = await drain(pws, timeout=0.5)
            self.assertFalse(
                any(m.get("type") == "standby_called" for m in msgs),
                "observer was able to trigger a standby",
            )

            await pws.close()
            await ows.close()
            await cws.close()
        asyncio.run(run())

    def test_observer_sees_caller_departure_then_takeover_works(self):
        async def run():
            cws, _, _ = await connect_caller("c1")
            ows, _ = await connect_observer()
            json.loads(await ows.recv())

            await cws.close()
            deadline = time.time() + 3.0
            saw_disconnect = False
            while time.time() < deadline and not saw_disconnect:
                raw = await asyncio.wait_for(ows.recv(), timeout=deadline - time.time())
                msg = json.loads(raw)
                if msg.get("type") == "full_state" and not msg["caller_connected"]:
                    saw_disconnect = True
            self.assertTrue(saw_disconnect, "observer was not told the caller left")

            # The observer device can now claim the caller seat (manual takeover)
            cws2, role, _ = await connect_caller("observer-device")
            self.assertEqual(role["type"], "role_assigned")
            state = await recv_type(ows, "full_state")
            self.assertTrue(state["caller_connected"])

            await ows.close()
            await cws2.close()
        asyncio.run(run())


class TestFlashAll(CueLightTestCase):
    """Pre-show roll call: the caller flashes every connected position's
    screen; each operator taps to confirm they're present."""

    def test_flash_reaches_positions_and_ack_confirms(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws1, _ = await connect_position("p1", "LX")
            await drain(cws)
            pws2, _ = await connect_position("p2", "SND")
            await drain(cws)

            await cws.send(json.dumps({"type": "flash_all"}))
            await recv_type(pws1, "flash")
            await recv_type(pws2, "flash")
            state = await recv_type(cws, "full_state")
            self.assertEqual(state["positions"]["p1"]["flash"], "pending")
            self.assertEqual(state["positions"]["p2"]["flash"], "pending")

            await pws1.send(json.dumps({"type": "ack_flash"}))
            state = await recv_type(cws, "full_state")
            self.assertEqual(state["positions"]["p1"]["flash"], "confirmed")
            self.assertEqual(state["positions"]["p2"]["flash"], "pending")

            await pws1.close()
            await pws2.close()
            await cws.close()
        asyncio.run(run())

    def test_disconnected_position_not_flashed(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws1, _ = await connect_position("p1", "LX")
            await drain(cws)
            await pws1.close()
            await asyncio.sleep(0.2)
            await drain(cws)

            await cws.send(json.dumps({"type": "flash_all"}))
            state = await recv_type(cws, "full_state")
            self.assertEqual(state["positions"]["p1"]["flash"], "none")
            await cws.close()
        asyncio.run(run())

    def test_ack_without_pending_ignored(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws1, _ = await connect_position("p1", "LX")
            await drain(cws)
            await pws1.send(json.dumps({"type": "ack_flash"}))
            msgs = await drain(cws, timeout=0.4)
            for m in msgs:
                if m.get("type") == "full_state":
                    self.assertEqual(m["positions"]["p1"]["flash"], "none")
            await pws1.close()
            await cws.close()
        asyncio.run(run())

    def test_clear_flash(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws1, _ = await connect_position("p1", "LX")
            await drain(cws)
            await cws.send(json.dumps({"type": "flash_all"}))
            await recv_type(pws1, "flash")
            await drain(cws)
            await cws.send(json.dumps({"type": "clear_flash"}))
            state = await recv_type(cws, "full_state")
            self.assertEqual(state["positions"]["p1"]["flash"], "none")
            await pws1.close()
            await cws.close()
        asyncio.run(run())


class TestAutoStandby(CueLightTestCase):
    """Optional mode: after master GO advances the cue, standby is called
    automatically on the next cue's targets. Off by default."""

    def test_off_by_default(self):
        async def run():
            cws, _, state = await connect_caller()
            self.assertFalse(state.get("auto_standby", False))
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)
            await cws.send(json.dumps({"type": "load_showfile", "filename": "example.json"}))
            await drain(cws)
            await drain(pws)

            await cws.send(json.dumps({"type": "go_armed"}))
            await recv_type(pws, "go_called")
            msgs = await drain(pws, timeout=0.5)
            self.assertFalse(
                any(m.get("type") == "standby_called" for m in msgs),
                "standby was auto-called with the option off",
            )
            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_enabled_calls_next_cue_targets(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws_lx, _ = await connect_position("p1", "LX")
            await drain(cws)
            pws_snd, _ = await connect_position("p2", "SND")
            await drain(cws)
            # example.json: cue 1 targets LX+SND, cue 2 targets LX only
            await cws.send(json.dumps({"type": "load_showfile", "filename": "example.json"}))
            await drain(cws)
            await drain(pws_lx)
            await drain(pws_snd)

            await cws.send(json.dumps({"type": "set_auto_standby", "enabled": True}))
            state = await recv_type(cws, "full_state")
            self.assertTrue(state["auto_standby"])

            await cws.send(json.dumps({"type": "go_armed"}))
            await recv_type(pws_lx, "go_called")
            await recv_type(pws_lx, "standby_called")
            await recv_type(pws_snd, "go_called")
            msgs = await drain(pws_snd, timeout=0.5)
            self.assertFalse(
                any(m.get("type") == "standby_called" for m in msgs),
                "SND is not in cue 2 but was auto-standby'd",
            )

            fs = [m for m in await drain(cws) if m.get("type") == "full_state"]
            self.assertTrue(fs)
            last = fs[-1]
            self.assertEqual(last["positions"]["p1"]["standby"], "called")
            self.assertEqual(last["positions"]["p2"]["standby"], "idle")

            await pws_lx.close()
            await pws_snd.close()
            await cws.close()
        asyncio.run(run())

    def test_auto_standby_persisted(self):
        async def run():
            cws, _, _ = await connect_caller()
            await cws.send(json.dumps({"type": "set_auto_standby", "enabled": True}))
            await recv_type(cws, "full_state")
            await asyncio.sleep(0.3)
            data = json.loads(STATE_SNAPSHOT.read_text())
            self.assertTrue(data["auto_standby"])
            await cws.close()
        asyncio.run(run())


class TestExitResume(CueLightTestCase):
    """EXIT archives the show to snapshot.bak instead of deleting it;
    the caller can resume the archived show."""

    BAK = PROJECT_ROOT / "state" / "snapshot.bak"

    def _post(self, path: str) -> dict:
        req = urllib_request.Request(f"{HTTP_URL}{path}", data=b"", method="POST")
        return json.loads(urllib_request.urlopen(req, timeout=2).read())

    def _get(self, path: str) -> dict:
        return json.loads(urllib_request.urlopen(f"{HTTP_URL}{path}", timeout=2).read())

    def test_exit_archives_snapshot(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)
            await asyncio.sleep(0.2)  # let the debounced snapshot write land
            await cws.send(json.dumps({"type": "exit"}))
            await asyncio.sleep(0.4)

            self.assertTrue(self.BAK.exists(), "EXIT did not archive snapshot.bak")
            data = json.loads(self.BAK.read_text())
            self.assertIn("p1", data["positions"])

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_backup_info_and_resume(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)
            await cws.send(json.dumps({"type": "load_showfile", "filename": "example.json"}))
            await drain(cws)
            await drain(pws)
            await asyncio.sleep(0.2)
            await cws.send(json.dumps({"type": "exit"}))
            await asyncio.sleep(0.4)

            info = self._get("/api/backup_info")
            self.assertTrue(info["exists"])
            self.assertEqual(info["showfile_filename"], "example.json")

            # A caller connects to the now-empty show, then resumes the old one
            cws2, _, state = await connect_caller("c2")
            self.assertEqual(state["positions"], {})
            resp = self._post("/api/resume_show")
            self.assertTrue(resp["ok"])

            deadline = time.time() + 3.0
            restored = None
            while time.time() < deadline:
                raw = await asyncio.wait_for(cws2.recv(), timeout=deadline - time.time())
                msg = json.loads(raw)
                if msg.get("type") == "full_state" and "p1" in msg.get("positions", {}):
                    restored = msg
                    if msg.get("showfile"):
                        break
            self.assertIsNotNone(restored, "caller never received the restored state")
            self.assertEqual(restored["positions"]["p1"]["label"], "LX")
            self.assertFalse(restored["positions"]["p1"]["connected"])
            self.assertIsNotNone(restored["showfile"], "showfile was not reloaded on resume")

            # The backup is consumed by the resume
            self.assertFalse(self._get("/api/backup_info")["exists"])

            await cws2.close()
        asyncio.run(run())

    def test_resume_without_backup_fails(self):
        if self.BAK.exists():
            self.BAK.unlink()
        resp = self._post("/api/resume_show")
        self.assertFalse(resp["ok"])

    def test_showlog_restored_on_resume(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)
            await cws.send(json.dumps({"type": "standby", "client_id": "p1"}))
            await recv_type(pws, "standby_called")
            await cws.send(json.dumps({"type": "exit"}))
            await asyncio.sleep(0.4)

            self.assertTrue(self._post("/api/resume_show")["ok"])
            await asyncio.sleep(0.2)
            data = self._get("/api/showlog")
            events = [e["event"] for e in data["entries"]]
            self.assertIn("standby_called", events, "pre-exit events lost on resume")
            self.assertIn("show_resumed", events)

            await pws.close()
            await cws.close()
        asyncio.run(run())


class TestShowLog(CueLightTestCase):
    """The server keeps a timestamped event log, downloadable as JSON or CSV."""

    def _get_log(self, fmt: str = "") -> str:
        url = f"{HTTP_URL}/api/showlog" + (f"?format={fmt}" if fmt else "")
        return urllib_request.urlopen(url, timeout=2).read().decode()

    def test_button_events_recorded_in_order(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)

            await cws.send(json.dumps({"type": "standby", "client_id": "p1"}))
            await recv_type(pws, "standby_called")
            await pws.send(json.dumps({"type": "ack_standby"}))
            await recv_type(cws, "full_state")
            await cws.send(json.dumps({"type": "go", "client_id": "p1"}))
            await recv_type(pws, "go_called")
            await pws.send(json.dumps({"type": "ack_go"}))
            await asyncio.sleep(0.3)

            data = json.loads(self._get_log())
            events = [(e["event"], e["position"]) for e in data["entries"]]
            self.assertIn(("position_joined", "LX"), events)
            expected = [
                ("standby_called", "LX"),
                ("standby_acked", "LX"),
                ("go_called", "LX"),
                ("go_acked", "LX"),
            ]
            for pair in expected:
                self.assertIn(pair, events)
            idxs = [events.index(pair) for pair in expected]
            self.assertEqual(idxs, sorted(idxs), "events logged out of order")
            for e in data["entries"]:
                self.assertIn("time", e)

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_csv_download(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)
            await cws.send(json.dumps({"type": "standby", "client_id": "p1"}))
            await recv_type(pws, "standby_called")
            await asyncio.sleep(0.3)

            text = self._get_log("csv")
            lines = text.strip().splitlines()
            self.assertEqual(lines[0].strip(), "time,event,position,cue,detail")
            self.assertTrue(
                any("standby_called" in ln and "LX" in ln for ln in lines[1:]),
                f"no standby_called row in CSV:\n{text}",
            )

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_cue_advance_logged(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)
            await cws.send(json.dumps({"type": "load_showfile", "filename": "example.json"}))
            await drain(cws)
            await drain(pws)
            await cws.send(json.dumps({"type": "go_armed"}))
            await drain(cws)
            await drain(pws)
            await asyncio.sleep(0.2)

            data = json.loads(self._get_log())
            events = [e["event"] for e in data["entries"]]
            self.assertIn("showfile_loaded", events)
            self.assertIn("master_go", events)
            self.assertIn("cue_advanced", events)
            adv = next(e for e in data["entries"] if e["event"] == "cue_advanced")
            self.assertEqual(adv["cue"], "2")

            await pws.close()
            await cws.close()
        asyncio.run(run())

    def test_exit_starts_fresh_log(self):
        async def run():
            cws, _, _ = await connect_caller()
            pws, _ = await connect_position("p1", "LX")
            await drain(cws)
            await cws.send(json.dumps({"type": "standby", "client_id": "p1"}))
            await recv_type(pws, "standby_called")
            await cws.send(json.dumps({"type": "exit"}))
            await asyncio.sleep(0.4)

            data = json.loads(self._get_log())
            events = [e["event"] for e in data["entries"]]
            self.assertNotIn("standby_called", events)

            await pws.close()
            await cws.close()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
