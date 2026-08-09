"""Local backend — WiFi/network settings API for the on-device settings menu
(see SLIDE_ANNOUNCER.md, "Kiosk display", "Local settings menu"), plus the
local-status endpoint the kiosk home page polls. Pairing and slide sync are
still not implemented (that's a separate build against the not-yet-built
server-side pairing API).
"""
import json
import socket
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import network
import system_control

app = FastAPI()

SETUP_MODE_STATUS = Path("/data/status/setup-mode.json")
VERSION_FILE = Path("/opt/slide-announcer/VERSION")


@app.get("/api/local/status")
def local_status():
    setup_info = {}
    if SETUP_MODE_STATUS.exists():
        try:
            setup_info = json.loads(SETUP_MODE_STATUS.read_text())
        except json.JSONDecodeError:
            pass

    return {
        "status": "not_configured",
        "message": "Slide Announcer image booted successfully. Pairing not yet implemented.",
        "hostname": socket.gethostname(),
        "image_version": VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else None,
        "setup_mode": setup_info.get("setup_mode"),
        "device_uuid": setup_info.get("device_uuid"),
    }


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
