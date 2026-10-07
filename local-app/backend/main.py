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
import re
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import captive_portal
import heartbeat
import network
import network_diagnostics
import pairing
import pinning
import revelation
import server_check
import srt_sink
import srt_stream_bridge
import sync
import system_control
import widgets

SETUP_MODE_STATUS = Path("/data/status/setup-mode.json")
VERSION_FILE = Path("/opt/slide-announcer/VERSION")


@asynccontextmanager
async def lifespan(app: FastAPI):
    heartbeat_task = asyncio.create_task(heartbeat.run_forever())
    sync_task = asyncio.create_task(sync.run_forever())
    srt_stream_task = asyncio.create_task(srt_stream_bridge.run_forever())
    yield
    heartbeat_task.cancel()
    sync_task.cancel()
    srt_stream_task.cancel()


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
    # Every language is synced to every device; only slides in this
    # device's language (or untagged ones) play. Filtering here, at read
    # time, means a language change applies at once rather than after the
    # next sync. With no language known yet, nothing is filtered.
    language = pairing.read_effective_language()
    if language:
        shows = [
            {**show, "slides": [s for s in show["slides"] if s.get("language") in (None, language)]}
            for show in shows
        ]
    return {
        "shows": shows,
        "settings": sync.read_settings(),
        "location": sync.read_location(),
        "pinned_show_id": _resolve_pinned_show_id(shows),
    }


@app.get("/api/local/widget-data/{overlay_id}/{element}/{endpoint}")
async def widget_data(overlay_id: int, element: str, endpoint: str, request: Request):
    # A widget's api.fetch() — forwarded to the server by reference (never
    # a URL), with the last good answer served while offline. widgets.py.
    status, body = await widgets.fetch_data(overlay_id, element, endpoint, request.query_params.multi_items())
    return JSONResponse(body, status_code=status, headers={
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    })


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


def _srt_sink_response(config: dict) -> dict:
    return {
        **config,
        "effective_enabled": srt_sink.effective_enabled(config),
        # Built here, not in the frontend, so the URL format (port,
        # mode=caller, latency) lives in exactly one place — srt_sink.py.
        # For the configured mode (SRT, RIST unicast, or the multicast group
        # a RIST sender transmits to) — see srt_sink.sender_url().
        "connect_url": srt_sink.sender_url(socket.gethostname(), config),
        "rist_supported": srt_sink.rist_supported(),
        "rist_port": srt_sink.RIST_PORT,
    }


@app.get("/api/local/srt-sink")
def srt_sink_status():
    return _srt_sink_response(srt_sink.read_config())


class SrtSinkEnableRequest(BaseModel):
    enabled: bool


@app.post("/api/local/srt-sink")
def srt_sink_set(body: SrtSinkEnableRequest):
    return _srt_sink_response(srt_sink.set_local_enabled(body.enabled))


class SrtSinkSettingsRequest(BaseModel):
    # All optional — only the fields sent are changed.
    mode: str | None = None
    passphrase: str | None = None
    multicast_group: str | None = None
    multicast_port: int | None = None
    multicast_passphrase: str | None = None
    rist_buffer_ms: int | None = None
    rist_encryption_bits: int | None = None


@app.post("/api/local/srt-sink/settings")
def srt_sink_update_settings(body: SrtSinkSettingsRequest):
    try:
        config = srt_sink.update_settings(body.model_dump(exclude_none=True))
    except srt_sink.SrtSinkConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _srt_sink_response(config)


@app.post("/api/local/srt-sink/regenerate")
def srt_sink_regenerate():
    return _srt_sink_response(srt_sink.regenerate_passphrase())


class SrtSinkLatencyRequest(BaseModel):
    latency_ms: int


@app.post("/api/local/srt-sink/latency")
def srt_sink_set_latency(body: SrtSinkLatencyRequest):
    return _srt_sink_response(srt_sink.set_srt_latency_ms(body.latency_ms))


class SrtSinkDebugOverlayRequest(BaseModel):
    enabled: bool


@app.post("/api/local/srt-sink/debug-overlay")
def srt_sink_set_debug_overlay(body: SrtSinkDebugOverlayRequest):
    return _srt_sink_response(srt_sink.set_debug_overlay(body.enabled))


@app.get("/api/local/srt-sink/playing")
def srt_sink_playing():
    # debug_overlay rides along here (already polled every second by
    # Slideshow.vue) rather than needing its own poll — see
    # srt_sink.set_debug_overlay()'s own comment.
    return {
        "active": srt_stream_bridge.is_playing(),
        "debug_overlay": srt_sink.read_config()["debug_overlay"],
    }


@app.websocket("/api/local/srt-sink/stream")
async def srt_sink_stream(websocket: WebSocket):
    # srt_stream_bridge.serve_client() replays a cached init segment +
    # recent fragments on connect (see that module's docstring) so a
    # kiosk page reload mid-stream can rejoin without waiting for the
    # source's next keyframe, then forwards live fragments until either
    # side disconnects.
    await websocket.accept()
    await srt_stream_bridge.serve_client(websocket)


class SrtSinkClientLogRequest(BaseModel):
    message: str


@app.post("/api/local/srt-sink/client-log")
def srt_sink_client_log(body: SrtSinkClientLogRequest):
    # Fire-and-forget relay from srtStreamPlayer.js (frontend/src/
    # srtStreamPlayer.js's logClient()) — a kiosk has no one watching
    # devtools, so MSE/WebSocket failures on that side would otherwise be
    # invisible. Lands in the same journal as srt_stream_bridge.py's own
    # logging (`journalctl -u slide-announcer-backend`).
    print(f"[srt-stream-bridge] client: {body.message}", flush=True)
    return {"ok": True}


@app.get("/api/local/revelation/scan")
async def revelation_scan():
    discovered = await asyncio.to_thread(revelation.discover)
    return {"discovered": discovered}


@app.get("/api/local/revelation/enabled")
def revelation_enabled_status():
    return {"enabled": revelation.read_enabled()}


class RevelationEnabledRequest(BaseModel):
    enabled: bool


@app.post("/api/local/revelation/enabled")
def revelation_enabled_set(body: RevelationEnabledRequest):
    return {"enabled": revelation.write_enabled(body.enabled)}


@app.get("/api/local/revelation/status")
def revelation_status():
    return revelation.read_status()


class RevelationPairRequest(BaseModel):
    host: str
    port: int
    pin: str


@app.post("/api/local/revelation/pair")
async def revelation_pair(body: RevelationPairRequest):
    try:
        data = await revelation.pair(body.host, body.port, body.pin)
    except revelation.RevelationPeerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, **data}


class RevelationUnpairRequest(BaseModel):
    instance_id: str


@app.post("/api/local/revelation/unpair")
def revelation_unpair(body: RevelationUnpairRequest):
    revelation.unpair(body.instance_id)
    return {"ok": True}


@app.get("/api/local/revelation/display-settings")
def revelation_display_settings():
    return revelation.read_display_settings()


class RevelationDisplaySettingsRequest(BaseModel):
    variant: str | None = None
    lang: str | None = None


@app.post("/api/local/revelation/display-settings")
def revelation_set_display_settings(body: RevelationDisplaySettingsRequest):
    try:
        return revelation.write_display_settings(body.variant, body.lang)
    except revelation.RevelationPeerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
