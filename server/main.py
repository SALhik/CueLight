from __future__ import annotations

import io
import socket
from pathlib import Path

import qrcode
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .persistence import load_state
from .showfile import list_showfiles, load_showfile, save_showfile, validate_showfile
from .state import StateManager
from .ws import caller_ws_handler, position_ws_handler

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="CueLight")
manager = StateManager(load_state())

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# --- HTML pages ---

@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "caller.html").read_text()


@app.get("/join", response_class=HTMLResponse)
async def join_page():
    return (STATIC_DIR / "join.html").read_text()


@app.get("/position", response_class=HTMLResponse)
async def position_page():
    return (STATIC_DIR / "position.html").read_text()


@app.get("/editor", response_class=HTMLResponse)
async def editor_page():
    return (STATIC_DIR / "editor.html").read_text()


# --- API ---

@app.get("/api/info")
async def api_info():
    ip = _get_local_ip()
    return {
        "ip": ip,
        "port": 8000,
        "caller_connected": manager.state.caller_connected,
        "password_enabled": manager.state.password_enabled,
    }


@app.post("/api/check_password")
async def check_password(request: Request):
    data = await request.json()
    ok = manager.check_password(data.get("password", ""))
    return {"ok": ok}


@app.post("/api/check_label")
async def check_label(request: Request):
    data = await request.json()
    label = data.get("label", "").strip()
    client_id = data.get("client_id", "")
    if not label:
        return {"ok": False, "reason": "Label cannot be empty."}
    for cid, pos in manager.state.positions.items():
        if cid != client_id and pos.label.lower() == label.lower():
            return {"ok": False, "reason": f'Label "{pos.label}" is already in use.'}
    return {"ok": True}


@app.get("/api/showfiles")
async def api_showfiles():
    return {"files": list_showfiles()}


@app.get("/api/showfile/{filename}")
async def api_get_showfile(filename: str):
    import json
    from .showfile import SHOWFILES_DIR
    path = SHOWFILES_DIR / filename
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    data = json.loads(path.read_text())
    return data


@app.post("/api/showfile/{filename}")
async def api_save_showfile(filename: str, request: Request):
    data = await request.json()
    errors = validate_showfile(data)
    if errors:
        return JSONResponse({"errors": errors}, status_code=400)
    save_showfile(filename, data)
    return {"ok": True}


@app.get("/api/qr")
async def api_qr(password: str = ""):
    ip = _get_local_ip()
    url = f"http://{ip}:8000/join"
    if password:
        url += f"?pw={password}"
    img = qrcode.make(url, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


# --- Test support ---

@app.post("/api/_test_reset")
async def test_reset():
    """Reset all server state. For test use only."""
    await manager.exit_show()
    return {"ok": True}


# --- WebSocket ---

@app.websocket("/ws/caller")
async def ws_caller(ws: WebSocket):
    await caller_ws_handler(ws, manager)


@app.websocket("/ws/position")
async def ws_position(ws: WebSocket):
    await position_ws_handler(ws, manager)
