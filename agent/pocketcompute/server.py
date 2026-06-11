"""FastAPI application: REST API, realtime WebSocket, and the PWA itself.

A single process serves everything:
  * the mobile web app (static files in ../web)
  * REST endpoints for pairing, state, shortcuts, files
  * a WebSocket that streams metrics, command output and job updates
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import files as filesvc
from . import system
from .auth import issue_token, verify_pairing_secret, verify_token
from .config import config
from .executor import available_shells, run_stream
from .jobs import jobs

WEB_DIR = (Path(__file__).resolve().parent.parent.parent / "web").resolve()

app = FastAPI(title="PocketCompute Agent", version="0.1.0")


# --------------------------------------------------------------------------- #
# Connection manager — tracks live WebSockets and broadcasts events to them.
# --------------------------------------------------------------------------- #
class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, event: str, payload: dict[str, Any]) -> None:
        if not self.clients:
            return
        message = json.dumps({"type": event, **payload})
        dead: list[WebSocket] = []
        for ws in list(self.clients):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


hub = Hub()
# Wire job events into the hub.
jobs.broadcast = hub.broadcast


# --------------------------------------------------------------------------- #
# Auth dependency for REST routes.
# --------------------------------------------------------------------------- #
def require_auth(authorization: str | None = None, token: str | None = Query(default=None)) -> dict:
    from fastapi import Header  # local import to keep signature clean
    raise RuntimeError("placeholder")  # replaced below


async def auth_dep(request) -> dict:  # noqa: ANN001
    header = request.headers.get("authorization", "")
    token = None
    if header.lower().startswith("bearer "):
        token = header[7:]
    if not token:
        token = request.query_params.get("token")
    claims = verify_token(token)
    if not claims:
        raise HTTPException(status_code=401, detail="Not paired / invalid token")
    return claims


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class PairRequest(BaseModel):
    secret: str
    label: str | None = "phone"


class ShortcutRequest(BaseModel):
    name: str
    command: str
    shell: str = "powershell"
    emoji: str = "⚡"


class JobStartRequest(BaseModel):
    name: str
    command: str
    shell: str = "powershell"
    cwd: str | None = None


# --------------------------------------------------------------------------- #
# Pairing (unauthenticated) + state
# --------------------------------------------------------------------------- #
@app.post("/api/pair")
async def pair(req: PairRequest) -> dict:
    if not verify_pairing_secret(req.secret):
        raise HTTPException(status_code=403, detail="Invalid pairing code")
    token = issue_token(req.label or "phone")
    return {
        "token": token,
        "device_name": config.get("device_name"),
    }


@app.get("/api/state")
async def state(request) -> dict:  # noqa: ANN001
    await auth_dep(request)
    return {
        "device_name": config.get("device_name"),
        "info": system.static_info(),
        "metrics": system.metrics(),
        "shells": available_shells(),
        "shortcuts": config.list_shortcuts(),
        "jobs": jobs.list(),
        "files": {"roots": [{"name": r.name or str(r), "path": str(r)}
                            for r in filesvc.roots()]},
    }


@app.get("/api/metrics")
async def get_metrics(request) -> dict:  # noqa: ANN001
    await auth_dep(request)
    return system.metrics()


# --------------------------------------------------------------------------- #
# Shortcuts
# --------------------------------------------------------------------------- #
@app.get("/api/shortcuts")
async def get_shortcuts(request) -> dict:  # noqa: ANN001
    await auth_dep(request)
    return {"shortcuts": config.list_shortcuts()}


@app.post("/api/shortcuts")
async def create_shortcut(request, req: ShortcutRequest) -> dict:  # noqa: ANN001
    await auth_dep(request)
    sc = config.add_shortcut(req.name, req.command, req.shell, req.emoji)
    await hub.broadcast("shortcuts_changed", {"shortcuts": config.list_shortcuts()})
    return sc


@app.delete("/api/shortcuts/{shortcut_id}")
async def remove_shortcut(request, shortcut_id: str) -> dict:  # noqa: ANN001
    await auth_dep(request)
    ok = config.delete_shortcut(shortcut_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Shortcut not found")
    await hub.broadcast("shortcuts_changed", {"shortcuts": config.list_shortcuts()})
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #
@app.get("/api/files")
async def list_files(request, path: str | None = Query(default=None)) -> dict:  # noqa: ANN001
    await auth_dep(request)
    try:
        return filesvc.list_dir(path)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except NotADirectoryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/files/download")
async def download_file(request, path: str = Query(...)):  # noqa: ANN001
    await auth_dep(request)
    try:
        f = filesvc.file_for_download(path)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return FileResponse(str(f), filename=f.name)


@app.post("/api/files/upload")
async def upload_file(request, dest: str = Form(...), file: UploadFile = File(...)) -> dict:  # noqa: ANN001
    await auth_dep(request)
    data = await file.read()
    try:
        return filesvc.save_upload(dest, file.filename or "upload.bin", data)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


# --------------------------------------------------------------------------- #
# Jobs (REST mirror of the WS controls)
# --------------------------------------------------------------------------- #
@app.get("/api/jobs")
async def list_jobs(request) -> dict:  # noqa: ANN001
    await auth_dep(request)
    return {"jobs": jobs.list()}


@app.get("/api/jobs/{job_id}")
async def job_detail(request, job_id: str) -> dict:  # noqa: ANN001
    await auth_dep(request)
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.detail()


@app.post("/api/jobs")
async def start_job(request, req: JobStartRequest) -> dict:  # noqa: ANN001
    await auth_dep(request)
    job = await jobs.start(req.name, req.command, req.shell, req.cwd)
    return job.summary()


@app.post("/api/jobs/{job_id}/stop")
async def stop_job(request, job_id: str) -> dict:  # noqa: ANN001
    await auth_dep(request)
    ok = await jobs.stop(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Job not running")
    return {"ok": True}


# --------------------------------------------------------------------------- #
# WebSocket — realtime metrics, command streaming, job control
# --------------------------------------------------------------------------- #
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    token = ws.query_params.get("token")
    if not verify_token(token):
        await ws.close(code=4401)
        return
    await hub.connect(ws)
    try:
        # Send an initial snapshot immediately.
        await ws.send_text(json.dumps({
            "type": "snapshot",
            "metrics": system.metrics(),
            "jobs": jobs.list(),
            "device_name": config.get("device_name"),
        }))
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await handle_ws_message(ws, msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.disconnect(ws)


async def handle_ws_message(ws: WebSocket, msg: dict[str, Any]) -> None:
    kind = msg.get("type")

    if kind == "ping":
        await ws.send_text(json.dumps({"type": "pong", "ts": time.time()}))

    elif kind == "run":
        await _run_command(ws, msg.get("command", ""), msg.get("shell", "powershell"),
                           msg.get("cwd"), msg.get("req_id"))

    elif kind == "run_shortcut":
        sc = config.get_shortcut(msg.get("id", ""))
        if not sc:
            await ws.send_text(json.dumps({"type": "error", "message": "Shortcut not found"}))
            return
        await _run_command(ws, sc["command"], sc.get("shell", "powershell"),
                           None, msg.get("req_id"), label=sc["name"])

    elif kind == "job_start":
        await jobs.start(msg.get("name", ""), msg.get("command", ""),
                         msg.get("shell", "powershell"), msg.get("cwd"))

    elif kind == "job_stop":
        await jobs.stop(msg.get("id", ""))

    elif kind == "job_detail":
        job = jobs.get(msg.get("id", ""))
        if job:
            await ws.send_text(json.dumps({"type": "job_detail", **job.detail()}))


async def _run_command(ws: WebSocket, command: str, shell: str, cwd: str | None,
                       req_id: str | None, label: str | None = None) -> None:
    if not command.strip():
        return
    await ws.send_text(json.dumps({
        "type": "output_start", "req_id": req_id, "command": command,
        "shell": shell, "label": label,
    }))

    async def on_output(chunk: str) -> None:
        try:
            await ws.send_text(json.dumps({"type": "output", "req_id": req_id, "chunk": chunk}))
        except Exception:
            pass

    code = await run_stream(command, shell, cwd, on_output)
    await ws.send_text(json.dumps({"type": "output_end", "req_id": req_id, "exit_code": code}))


# --------------------------------------------------------------------------- #
# Background metrics broadcaster
# --------------------------------------------------------------------------- #
async def _metrics_loop() -> None:
    while True:
        await asyncio.sleep(2.0)
        if hub.clients:
            await hub.broadcast("metrics", {"metrics": system.metrics()})


@app.on_event("startup")
async def _startup() -> None:
    asyncio.create_task(_metrics_loop())


# --------------------------------------------------------------------------- #
# Static PWA — must be mounted last so it doesn't shadow /api routes.
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>PocketCompute</h1><p>Web assets missing.</p>", status_code=500)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "device": config.get("device_name")})


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
