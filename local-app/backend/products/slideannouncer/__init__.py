"""The original product: a paired Raspberry Pi that syncs a church's slide
shows from the AnnouncementSlides server and plays them full screen, with
optional LAN video receiving (SRT/RIST) and Revelation peering."""
from pathlib import Path

from product import Product

from . import pinning, srt_sink, srt_stream_bridge, sync
from .routes import router


def _heartbeat_payload() -> dict:
    return {
        # Only ever reported once generated (on this device's first SRT Sink
        # enable) — the server never sets this, only mirrors it for an admin
        # to read off the fleet dashboard. See srt_sink.py's own docstring.
        "srt_sink_passphrase": srt_sink.read_config()["passphrase"] or None,
        # Full LAN Video Receiver settings + the last web-side revision
        # applied — see srt_sink.py's SERVER_EDITABLE_FIELDS for the sync.
        "srt_sink_config": srt_sink.report(),
    }


def _on_heartbeat_response(response: dict) -> None:
    # Fleet-wide force-disable switch for SRT Sink (admin dashboard) — an
    # explicit false always overrides this device's own local Settings
    # toggle; see srt_sink.py's effective_enabled(). Missing key (older
    # server) or true both mean "no restriction."
    srt_sink.set_server_allows(response.get("srt_sink_enabled", True) is not False)
    # Receiver settings edited on the web page (no-op unless newer than the
    # last revision applied) — the slide sync's response carries the same
    # push, so this usually arrives there first.
    srt_sink.apply_server_config(response.get("srt_sink_config"))


product = Product(
    name="slideannouncer",
    api_base="/api/slide-announcers",
    routers=[router],
    background_tasks=[sync.run_forever, srt_stream_bridge.run_forever],
    status_fields=lambda: {"sync": sync.read_status()},
    heartbeat_payload=_heartbeat_payload,
    on_heartbeat_response=_on_heartbeat_response,
    wipe_paths=[
        Path("/data/slides"),
        Path("/data/local-app/settings.json"),
        # A pinned show id is meaningless once unpaired.
        pinning.PINNED_SHOW_ID_FILE,
    ],
)
