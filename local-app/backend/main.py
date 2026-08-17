"""Local backend — WiFi/network settings API for the on-device settings menu
(see SLIDE_ANNOUNCER.md, "Kiosk display", "Local settings menu"), the
pairing screen's API, the heartbeat and slide-sync background tasks, the
local-status endpoint the kiosk home page polls, and the slideshow endpoint
the kiosk display (frontend/src/views/Slideshow.vue) polls for the cached
playlist/settings sync.py maintains on disk.
"""
import asyncio
import json
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import heartbeat
import network
import pairing
import sync
import system_control

SETUP_MODE_STATUS = Path("/data/status/setup-mode.json")
VERSION_FILE = Path("/opt/slide-announcer/VERSION")


@asynccontextmanager
async def lifespan(app: FastAPI):
    heartbeat_task = asyncio.create_task(heartbeat.run_forever())
    sync_task = asyncio.create_task(sync.run_forever())
    yield
    heartbeat_task.cancel()
    sync_task.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/api/local/status")
def local_status():
    setup_info = {}
    if SETUP_MODE_STATUS.exists():
        try:
            setup_info = json.loads(SETUP_MODE_STATUS.read_text())
        except json.JSONDecodeError:
            pass

    paired = pairing.is_paired()

    return {
        "status": "paired" if paired else "not_paired",
        "message": "Slide Announcer paired." if paired else "Slide Announcer image booted successfully. Not yet paired.",
        "hostname": socket.gethostname(),
        "image_version": VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else None,
        "app_version": heartbeat.read_app_version(),
        "setup_mode": setup_info.get("setup_mode"),
        "device_uuid": setup_info.get("device_uuid"),
        "paired": paired,
        "paired_at": pairing.read_paired_at(),
        "device_name": pairing.read_device_name(),
        "entity_name": pairing.read_entity_name(),
        "heartbeat": heartbeat.read_status(),
        "sync": sync.read_status(),
    }


@app.get("/api/local/sync/status")
def sync_status():
    return sync.read_status()


@app.get("/api/local/slideshow")
def slideshow():
    return {"playlist": sync.read_playlist(), "settings": sync.read_settings()}


class PairRequest(BaseModel):
    code: str
    device_name: str


@app.post("/api/local/pair")
async def pair(body: PairRequest):
    try:
        data = await pairing.pair(body.code, body.device_name)
    except pairing.PairingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "slide_announcer_id": data["slide_announcer_id"]}


@app.post("/api/local/unpair")
async def unpair():
    pairing.unpair_and_wipe()
    return {"ok": True}


@app.get("/api/local/network/status")
async def network_status():
    try:
        status = await network.get_status()
    except network.NetworkCommandError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return status


@app.get("/api/local/network/scan")
async def network_scan():
    try:
        access_points = await network.scan_access_points()
    except network.NetworkCommandError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"access_points": access_points}


class ConnectRequest(BaseModel):
    ssid: str
    password: str | None = None


@app.post("/api/local/network/connect")
async def network_connect(body: ConnectRequest):
    try:
        await network.connect(body.ssid, body.password)
    except network.NetworkCommandError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        connectivity = await network.check_connectivity()
    except network.NetworkCommandError:
        connectivity = "unknown"

    try:
        status = await network.get_status()
    except network.NetworkCommandError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"connectivity": connectivity, "status": status}


class ForgetRequest(BaseModel):
    ssid: str


@app.post("/api/local/network/forget")
async def network_forget(body: ForgetRequest):
    try:
        await network.forget(body.ssid)
    except network.NetworkCommandError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/local/system/update-check")
def system_update_check_status():
    return {"result": system_control.read_update_check_status()}


@app.post("/api/local/system/update-check")
async def system_update_check_trigger():
    try:
        result = await system_control.trigger_update_check()
    except system_control.SystemCommandError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"result": result}


@app.post("/api/local/system/update-apply")
async def system_update_apply():
    try:
        result = await system_control.trigger_update_apply()
    except system_control.UpdateAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except system_control.SystemCommandError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"result": result}


@app.get("/api/local/system/update-progress")
async def system_update_progress():
    return {
        "running": await system_control.currently_running_update() is not None,
        "progress": system_control.read_update_progress(),
    }


@app.post("/api/local/system/reboot")
async def system_reboot():
    try:
        await system_control.reboot()
    except system_control.SystemCommandError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/local/system/factory-reset")
async def system_factory_reset():
    try:
        await system_control.trigger_factory_reset()
    except system_control.SystemCommandError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True}
