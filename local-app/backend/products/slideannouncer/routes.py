"""The slideannouncer product's `/api/local/*` endpoints — slides, pinned
show, widget data, LAN video receiver (SRT/RIST) and Revelation peering.
Mounted by main.py through products.slideannouncer.product.routers."""
import asyncio
import socket

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import pairing

from . import pinning, revelation, srt_sink, srt_stream_bridge, sync, widgets

router = APIRouter()


@router.get("/api/local/sync/status")
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


@router.get("/api/local/slideshow")
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


@router.get("/api/local/widget-data/{overlay_id}/{element}/{endpoint}")
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


@router.post("/api/local/pin-show")
def pin_show(body: PinShowRequest):
    # Local-only action — no server round trip. The main Laravel backend
    # has no concept of "what a kiosk currently has pinned"; see
    # MULTI_SHOW_IMPLEMENTATION.md.
    pinning.write_pinned_show_id(body.show_id)
    return {"ok": True, "pinned_show_id": body.show_id}


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


@router.get("/api/local/srt-sink")
def srt_sink_status():
    return _srt_sink_response(srt_sink.read_config())


class SrtSinkEnableRequest(BaseModel):
    enabled: bool


@router.post("/api/local/srt-sink")
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


@router.post("/api/local/srt-sink/settings")
def srt_sink_update_settings(body: SrtSinkSettingsRequest):
    try:
        config = srt_sink.update_settings(body.model_dump(exclude_none=True))
    except srt_sink.SrtSinkConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _srt_sink_response(config)


@router.post("/api/local/srt-sink/regenerate")
def srt_sink_regenerate():
    return _srt_sink_response(srt_sink.regenerate_passphrase())


class SrtSinkLatencyRequest(BaseModel):
    latency_ms: int


@router.post("/api/local/srt-sink/latency")
def srt_sink_set_latency(body: SrtSinkLatencyRequest):
    return _srt_sink_response(srt_sink.set_srt_latency_ms(body.latency_ms))


class SrtSinkDebugOverlayRequest(BaseModel):
    enabled: bool


@router.post("/api/local/srt-sink/debug-overlay")
def srt_sink_set_debug_overlay(body: SrtSinkDebugOverlayRequest):
    return _srt_sink_response(srt_sink.set_debug_overlay(body.enabled))


@router.get("/api/local/srt-sink/playing")
def srt_sink_playing():
    # debug_overlay rides along here (already polled every second by
    # Slideshow.vue) rather than needing its own poll — see
    # srt_sink.set_debug_overlay()'s own comment.
    return {
        "active": srt_stream_bridge.is_playing(),
        "debug_overlay": srt_sink.read_config()["debug_overlay"],
    }


@router.websocket("/api/local/srt-sink/stream")
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


@router.post("/api/local/srt-sink/client-log")
def srt_sink_client_log(body: SrtSinkClientLogRequest):
    # Fire-and-forget relay from srtStreamPlayer.js (frontend/src/
    # srtStreamPlayer.js's logClient()) — a kiosk has no one watching
    # devtools, so MSE/WebSocket failures on that side would otherwise be
    # invisible. Lands in the same journal as srt_stream_bridge.py's own
    # logging (`journalctl -u slide-announcer-backend`).
    print(f"[srt-stream-bridge] client: {body.message}", flush=True)
    return {"ok": True}


@router.get("/api/local/revelation/scan")
async def revelation_scan():
    discovered = await asyncio.to_thread(revelation.discover)
    return {"discovered": discovered}


@router.get("/api/local/revelation/enabled")
def revelation_enabled_status():
    return {"enabled": revelation.read_enabled()}


class RevelationEnabledRequest(BaseModel):
    enabled: bool


@router.post("/api/local/revelation/enabled")
def revelation_enabled_set(body: RevelationEnabledRequest):
    return {"enabled": revelation.write_enabled(body.enabled)}


@router.get("/api/local/revelation/status")
def revelation_status():
    return revelation.read_status()


class RevelationPairRequest(BaseModel):
    host: str
    port: int
    pin: str


@router.post("/api/local/revelation/pair")
async def revelation_pair(body: RevelationPairRequest):
    try:
        data = await revelation.pair(body.host, body.port, body.pin)
    except revelation.RevelationPeerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, **data}


class RevelationUnpairRequest(BaseModel):
    instance_id: str


@router.post("/api/local/revelation/unpair")
def revelation_unpair(body: RevelationUnpairRequest):
    revelation.unpair(body.instance_id)
    return {"ok": True}


@router.get("/api/local/revelation/display-settings")
def revelation_display_settings():
    return revelation.read_display_settings()


class RevelationDisplaySettingsRequest(BaseModel):
    variant: str | None = None
    lang: str | None = None


@router.post("/api/local/revelation/display-settings")
def revelation_set_display_settings(body: RevelationDisplaySettingsRequest):
    try:
        return revelation.write_display_settings(body.variant, body.lang)
    except revelation.RevelationPeerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
