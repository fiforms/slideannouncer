"""Local backend — WiFi/network settings API for the on-device settings menu
(see SLIDE_ANNOUNCER.md, "Kiosk display", "Local settings menu"), the
pairing screen's API, the heartbeat and slide-sync background tasks, the
local-status endpoint the kiosk home page polls, and the slideshow endpoint
the kiosk display (frontend/src/views/Slideshow.vue) and Menu overlay
(MenuOverlay.vue) poll for the cached shows/settings sync.py maintains on
disk, plus the local-only show-pin endpoint (pinning.py).
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
import pinning
import srt_sink
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
        "language": pairing.read_effective_language(),
        "language_source": "server" if pairing.read_language() else ("boot_yaml" if pairing.read_language_boot_hint() else None),
        "heartbeat": heartbeat.read_status(),
        "sync": sync.read_status(),
    }


@app.get("/api/local/sync/status")
def sync_status():
    return sync.read_status()


def _resolve_pinned_show_id(shows: list) -> str | None:
    """The pin as the kiosk should actually use right now: the on-device
    pin if it's set and still among the synced shows, else the Main show,
    else (only if there are no shows at all) None. sync.py already clears
    a pin that's gone stale after a successful sync — this is cheap
    defense-in-depth for the window before that's happened."""
    pinned = pinning.read_pinned_show_id()
    if pinned and any(show["id"] == pinned for show in shows):
        return pinned
    main_show = next((show for show in shows if show.get("is_main")), None)
    if main_show:
        return main_show["id"]
    return shows[0]["id"] if shows else None


@app.get("/api/local/slideshow")
def slideshow():
    shows = sync.read_shows()
    return {
        "shows": shows,
        "settings": sync.read_settings(),
        "pinned_show_id": _resolve_pinned_show_id(shows),
    }


class PinShowRequest(BaseModel):
    show_id: str | None = None


@app.post("/api/local/pin-show")
def pin_show(body: PinShowRequest):
    # Local-only action — no server round trip. The main Laravel backend
    # has no concept of "what a kiosk currently has pinned"; see
    # MULTI_SHOW_IMPLEMENTATION.md.
    pinning.write_pinned_show_id(body.show_id)
    return {"ok": True, "pinned_show_id": body.show_id}


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
        status = await network.get_status()
    except network.NetworkCommandError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"connectivity": status.connectivity, "status": status}


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


@app.get("/api/local/audio-volume")
def audio_volume_status():
    # Deliberately its own tiny endpoint rather than folded into
    # /api/local/status: Slideshow.vue calls this once per volume/mute
    # keypress (debounced, not on a fixed interval — see that component's
    # own comment), and /api/local/status pulls in heavier heartbeat/sync
    # state this doesn't need.
    return {"volume": pairing.read_audio_volume(), "muted": pairing.read_audio_muted()}


def _srt_sink_response(config: dict) -> dict:
    return {
        **config,
        "effective_enabled": srt_sink.effective_enabled(config),
        # Built here, not in the frontend, so the URL format (port,
        # mode=caller, latency) lives in exactly one place — srt_sink.py.
        "connect_url": srt_sink.connect_url(socket.gethostname(), config["passphrase"])
        if config["passphrase"] else None,
    }


@app.get("/api/local/srt-sink")
def srt_sink_status():
    return _srt_sink_response(srt_sink.read_config())


class SrtSinkEnableRequest(BaseModel):
    enabled: bool


@app.post("/api/local/srt-sink")
def srt_sink_set(body: SrtSinkEnableRequest):
    return _srt_sink_response(srt_sink.set_local_enabled(body.enabled))


@app.post("/api/local/srt-sink/regenerate")
def srt_sink_regenerate():
    return _srt_sink_response(srt_sink.regenerate_passphrase())


@app.get("/api/local/srt-sink/playing")
def srt_sink_playing():
    return {"active": srt_sink.is_playing()}


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
