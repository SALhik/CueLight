from __future__ import annotations

import io
import json
import socket
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import qrcode
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .files import require_safe_filename
from .patch import list_patches, load_patch, save_patch, validate_patch
from .persistence import load_state
from .showcsv import csv_to_cues, cues_to_csv
from .showfile import list_showfiles, load_showfile, save_showfile, validate_showfile
from .showreport import report_csv, report_html
from .state import StateManager
from .ws import caller_ws_handler, observer_ws_handler, position_ws_handler

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

manager = StateManager(load_state())

MDNS_HOSTNAME = "cuelight"
_mdns: dict = {"zeroconf": None, "info": None, "host": ""}


def _start_mdns() -> None:
    """Best-effort: advertise cuelight.local via mDNS so operators can type a
    name instead of an IP. Any failure (zeroconf missing, name already taken
    on the LAN, odd network) just means falling back to the IP/QR flow.
    Registration probes the network for seconds, so it runs off the event loop."""
    threading.Thread(target=_register_mdns, daemon=True).start()


def _register_mdns() -> None:
    try:
        from zeroconf import ServiceInfo, Zeroconf
        ip = _get_local_ip()
        info = ServiceInfo(
            "_http._tcp.local.",
            "CueLight._http._tcp.local.",
            addresses=[socket.inet_aton(ip)],
            port=8000,
            server=f"{MDNS_HOSTNAME}.local.",
        )
        zc = Zeroconf()
        zc.register_service(info)
        _mdns.update(zeroconf=zc, info=info, host=f"{MDNS_HOSTNAME}.local")
    except Exception:
        _mdns.update(zeroconf=None, info=None, host="")


def _stop_mdns() -> None:
    zc, info = _mdns["zeroconf"], _mdns["info"]
    if zc is not None:
        try:
            if info is not None:
                zc.unregister_service(info)
            zc.close()
        except Exception:
            pass
    _mdns.update(zeroconf=None, info=None, host="")


async def _restore_files(manager: StateManager) -> None:
    """Reload the OSC patch and showfile named in the state by filename.
    Patch first so showfile arming includes OSC positions."""
    if manager.state.osc_patch_filename:
        try:
            patch = load_patch(manager.state.osc_patch_filename)
            await manager.load_patch(patch)
        except (FileNotFoundError, ValueError):
            await manager.clear_osc_patch_filename()
    if manager.state.showfile_filename:
        try:
            sf = load_showfile(manager.state.showfile_filename)
            await manager.restore_showfile(sf)
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            await manager.clear_showfile_filename()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await _restore_files(manager)
    _start_mdns()
    yield
    _stop_mdns()


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


@app.get("/observer", response_class=HTMLResponse)
async def observer_page():
    return (STATIC_DIR / "observer.html").read_text()


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
async def api_info(request: Request):
    ip = _get_local_ip()
    return {
        "ip": ip,
        "port": request.url.port or 8000,
        "mdns_host": _mdns["host"],
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
    from .showfile import SHOWFILES_DIR
    try:
        require_safe_filename(filename)
    except ValueError:
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    path = SHOWFILES_DIR / filename
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    data = json.loads(path.read_text())
    return data


@app.post("/api/showfile/{filename}")
async def api_save_showfile(filename: str, request: Request):
    try:
        require_safe_filename(filename)
    except ValueError:
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    data = await request.json()
    errors = validate_showfile(data)
    if errors:
        return JSONResponse({"errors": errors}, status_code=400)
    save_showfile(filename, data)
    return {"ok": True}


@app.post("/api/csv/import")
async def api_csv_import(request: Request):
    text = (await request.body()).decode("utf-8-sig", errors="replace")
    cues, errors = csv_to_cues(text)
    if errors:
        return JSONResponse({"errors": errors}, status_code=400)
    return {"cues": cues}


@app.post("/api/csv/export")
async def api_csv_export(request: Request):
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    return Response(
        cues_to_csv(data.get("cues", [])),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="showfile.csv"'},
    )


@app.get("/api/patches")
async def api_patches():
    return {"files": list_patches()}


@app.get("/api/patch/{filename}")
async def api_get_patch(filename: str):
    try:
        return load_patch(filename).to_dict()
    except FileNotFoundError:
        return JSONResponse({"error": "not found"}, status_code=404)
    except ValueError:
        return JSONResponse({"error": "invalid patch file"}, status_code=400)


@app.post("/api/patch/{filename}")
async def api_save_patch(filename: str, request: Request):
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    if filename == "_probe_test":
        return await manager.probe_test(data)

    try:
        require_safe_filename(filename)
    except ValueError:
        return JSONResponse({"error": "invalid filename"}, status_code=400)
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


@app.get("/api/backup_info")
async def api_backup_info():
    from .persistence import backup_info
    return backup_info()


@app.post("/api/resume_show")
async def api_resume_show():
    ok = await manager.resume_show()
    if ok:
        await _restore_files(manager)
    return {"ok": ok}


@app.get("/api/showlog")
async def api_showlog(format: str = "json"):
    if format == "csv":
        return Response(
            manager.log.to_csv(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="showlog.csv"'},
        )
    return {"entries": manager.log.entries}


@app.get("/api/showreport")
async def api_showreport(format: str = "html"):
    """Post-show report computed on demand from the show log; nothing is
    stored server-side. Exposed like /api/showlog (no password gate)."""
    if format == "csv":
        return Response(
            report_csv(manager.log.entries),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="showreport.csv"'},
        )
    return HTMLResponse(report_html(manager.log.entries))


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


@app.websocket("/ws/observer")
async def ws_observer(ws: WebSocket):
    await observer_ws_handler(ws, manager)
