from __future__ import annotations

import io
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import qrcode
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .patch import list_patches, load_patch, save_patch, validate_patch
from .persistence import load_state
from .showfile import list_showfiles, load_showfile, save_showfile, validate_showfile
from .state import StateManager
from .ws import caller_ws_handler, position_ws_handler

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

manager = StateManager(load_state())


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if manager.state.osc_patch_filename:
        try:
            patch = load_patch(manager.state.osc_patch_filename)
            await manager.load_patch(patch)
        except Exception:
            manager.state.osc_patch_filename = ""
    yield


app = FastAPI(title="CueLight", lifespan=_lifespan)

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


@app.get("/.well-known/appspecific/com.chrome.devtools.json")
async def chrome_devtools_json():
    return JSONResponse([])


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
async def apple_touch_icon():
    return Response(status_code=204)


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


@app.get("/api/patches")
async def api_patches():
    return {"files": list_patches()}


@app.get("/api/patch/{filename}")
async def api_get_patch(filename: str):
    import json
    from .patch import PATCHES_DIR
    path = PATCHES_DIR / filename
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    data = json.loads(path.read_text())
    return data


@app.post("/api/patch/{filename}")
async def api_save_patch(filename: str, request: Request):
    if filename == "_probe_test":
        from .models import OscDevice
        from .osc import probe as osc_probe
        data = await request.json()
        device = OscDevice(
            name=data.get("name", "test"),
            ip=data.get("ip", ""),
            port=data.get("port", 8000),
            protocol=data.get("protocol", "udp"),
            ping_template=data.get("ping_template", ""),
            expect_reply=data.get("expect_reply", False),
        )
        probe_state, trust = await osc_probe(device)
        return {"probe": probe_state.value, "trust": trust}
    data = await request.json()
    errors = validate_patch(data)
    if errors:
        return JSONResponse({"errors": errors}, status_code=400)
    save_patch(filename, data)
    return {"ok": True}


@app.get("/api/qr")
async def api_qr(request: Request, password: str = ""):
    host = request.headers.get("host") or f"{_get_local_ip()}:8000"
    url = f"http://{host}/join"
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
