"""Local backend — the product-neutral core of the on-device API: WiFi/
network settings, pairing, language/name/setup, audio/screen/system
control and update endpoints, and the local-status endpoint the kiosk
polls. Everything product-specific (the slideshow, pinned show, LAN video
receiver, …) arrives through product.get(): its routers are mounted below,
its background tasks run beside the heartbeat, and its status fields are
merged into /api/local/status. See product.py and docs/PRODUCTS.md.
"""
import asyncio
import json
import re
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import captive_portal
import heartbeat
import network
import network_diagnostics
import pairing
import product
import server_check
import system_control

SETUP_MODE_STATUS = Path("/data/status/setup-mode.json")
VERSION_FILE = Path("/opt/slide-announcer/VERSION")


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(run()) for run in [heartbeat.run_forever, *product.get().background_tasks]]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(lifespan=lifespan)
for _router in product.get().routers:
    app.include_router(_router)


@app.get("/api/local/status")
def local_status():
    setup_info = {}
    if SETUP_MODE_STATUS.exists():
        try:
            setup_info = json.loads(SETUP_MODE_STATUS.read_text())
        except json.JSONDecodeError:
            pass

    paired = pairing.is_paired()
    hostname = socket.gethostname()

    try:
        server_url = pairing.read_server_url()
    except pairing.PairingError:
        # read_server_url() fails closed (missing/blank config) rather than
        # returning a placeholder — surfaced here as a plain None instead
        # of a 500, since this is just a display value for the Pairing
        # screen's "go to {server_url}/slide-announcers" hint.
        server_url = None

    return {
        "status": "paired" if paired else "not_paired",
        "message": "Slide Announcer paired." if paired else "Slide Announcer image booted successfully. Not yet paired.",
        "hostname": hostname,
        # The first-run wizard (frontend views/setup/) launches while this
        # is false — see pairing.is_setup_complete().
        "setup_complete": pairing.is_setup_complete(),
        # A device name set in the wizard or at pairing has a new hostname
        # waiting on a reboot (firstboot.py's set_hostname()).
        "hostname_change_pending": pairing.hostname_change_pending(hostname),
        "server_url": server_url,
        "image_version": VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else None,
        "app_version": heartbeat.read_app_version(),
        "setup_mode": setup_info.get("setup_mode"),
        "device_uuid": setup_info.get("device_uuid"),
        "paired": paired,
        "paired_at": pairing.read_paired_at(),
        "device_name": pairing.read_device_name(),
        "entity_name": pairing.read_entity_name(),
        "language": pairing.read_effective_language(),
        "language_source": pairing.read_language_source(),
        "heartbeat": heartbeat.read_status(),
        "product": product.get().name,
        **product.get().status_fields(),
    }


class PairRequest(BaseModel):
    code: str
    device_name: str


@app.post("/api/local/pair")
async def pair(body: PairRequest):
    try:
        data = await pairing.pair(body.code, body.device_name)
    except pairing.PairingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # `device_id` is the contract's name (docs/DEVICE_CONTRACT.md); servers
    # that predate it send the id as `slide_announcer_id`.
    device_id = data.get("device_id", data.get("slide_announcer_id"))
    return {"ok": True, "device_id": device_id, "slide_announcer_id": device_id}


class LanguageRequest(BaseModel):
    language: str


@app.post("/api/local/language")
async def set_language(body: LanguageRequest):
    # The setup wizard's Welcome screen and Settings > Advanced. Applies
    # locally at once; when paired it's also pushed to the server right away
    # (rather than waiting for the next scheduled heartbeat) so the web page
    # and slide filtering follow. If the push fails, it stays queued for the
    # next heartbeat.
    code = body.language.strip().lower()
    if not re.fullmatch(r"[a-z]{2,3}", code):
        raise HTTPException(status_code=422, detail="Unsupported language code.")
    pairing.set_device_language(code)
    if pairing.read_pending_language():
        await heartbeat.send_once()
    return {"language": pairing.read_effective_language(), "language_source": pairing.read_language_source()}


class DeviceNameRequest(BaseModel):
    device_name: str


@app.post("/api/local/device-name")
def set_device_name(body: DeviceNameRequest):
    # Pre-pairing naming from the setup wizard. Once paired, the server owns
    # the name (renamed from the fleet UI, synced back by heartbeat.py).
    if pairing.is_paired():
        raise HTTPException(status_code=409, detail="This device is paired; rename it from the website.")
    hostname = pairing.set_local_device_name(body.device_name)
    return {"device_name": pairing.read_device_name(), "hostname": hostname}


@app.post("/api/local/setup/complete")
def setup_complete():
    pairing.mark_setup_complete()
    return {"ok": True}


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
    except network.ConnectError as exc:
        # Same `detail` shape as every other error, plus what the
        # WifiConnect screen needs to explain the failure.
        return JSONResponse(status_code=400, content={
            "detail": str(exc), "reason": exc.reason, "raw": exc.raw,
            "log": exc.log, "log_restricted": exc.log_restricted,
        })
    except network.NetworkCommandError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        status = await network.get_status()
    except network.NetworkCommandError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"connectivity": status.connectivity, "status": status}


@app.get("/api/local/network/diagnostics")
async def network_diagnostics_report(rescan: bool = False):
    # Slow (pings, DNS, a portal probe, the journal) — the page fetches it
    # on demand rather than as part of /network/status.
    return await network_diagnostics.collect(rescan=rescan)


@app.get("/api/local/network/server-check")
async def network_server_check():
    # Separate from /network/status so a slow or dead server never holds
    # up the rest of the Network page.
    return await server_check.check()


class PortalSignInRequest(BaseModel):
    return_path: str | None = None


@app.post("/api/local/network/portal/sign-in")
async def network_portal_sign_in(body: PortalSignInRequest | None = None):
    # Frontend navigates the kiosk tab to this URL; captive_portal's
    # watcher brings it back to the Network page it came from (Settings or
    # the setup wizard) once online.
    try:
        gateway = (await network.get_status()).gateway
    except network.NetworkCommandError:
        gateway = None
    return {"url": await captive_portal.start_sign_in(body.return_path if body else None, gateway)}


class ForgetRequest(BaseModel):
    ssid: str


@app.post("/api/local/network/forget")
async def network_forget(body: ForgetRequest):
    try:
        await network.forget(body.ssid)
    except network.NetworkCommandError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/local/audio-output")
def audio_output_status():
    return {"audio_output": pairing.read_audio_output()}


class AudioOutputRequest(BaseModel):
    value: str


@app.post("/api/local/audio-output")
async def audio_output_set(body: AudioOutputRequest):
    if body.value not in ("hdmi", "headphones"):
        raise HTTPException(status_code=422, detail="value must be 'hdmi' or 'headphones'")
    pairing.write_audio_output(body.value)
    await system_control.apply_audio_output()
    return {"ok": True, "audio_output": body.value}


@app.get("/api/local/screen-resolution")
def screen_resolution_status():
    return {"screen_resolution": pairing.read_screen_resolution()}


class ScreenResolutionRequest(BaseModel):
    value: str


@app.post("/api/local/screen-resolution")
async def screen_resolution_set(body: ScreenResolutionRequest):
    if body.value not in ("4k", "1080p"):
        raise HTTPException(status_code=422, detail="value must be '4k' or '1080p'")
    pairing.write_screen_resolution(body.value)
    await system_control.apply_screen_resolution()
    return {"ok": True, "screen_resolution": body.value}


@app.get("/api/local/audio-volume")
def audio_volume_status():
    # Deliberately its own tiny endpoint rather than folded into
    # /api/local/status: Slideshow.vue calls this once per volume/mute
    # keypress (debounced, not on a fixed interval — see that component's
    # own comment), and /api/local/status pulls in heavier heartbeat/sync
    # state this doesn't need.
    return {"volume": pairing.read_audio_volume(), "muted": pairing.read_audio_muted()}


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


@app.post("/api/local/system/sleep")
async def system_sleep():
    try:
        await system_control.sleep_display()
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
