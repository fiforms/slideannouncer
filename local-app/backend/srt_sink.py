"""On-demand SRT video-sink configuration — enable toggle + passphrase for
srt_stream_bridge.py, backing Settings > SRT Sink. Persisted at
/data/status/srt-sink.json, the same flat-file-under-/data/status pattern
pairing.py uses for audio output/volume.

The passphrase is never operator-typed: it's generated once, on-device,
the first time SRT Sink is enabled (ensure_passphrase()), and reported up
to the server on every heartbeat (see heartbeat.py's payload) purely so an
admin can read it off the fleet dashboard to configure their SRT sender —
the server never sets it, only mirrors it.

Two independent "should this run" inputs, both must be true for
srt_stream_bridge.py to actually listen — see effective_enabled():
- `local_enabled` — this device's own Settings > SRT Sink toggle.
- `server_allows` — the admin dashboard's force-disable switch, folded in
  from the heartbeat response (see heartbeat.py) the same way
  device_name/language already are. Missing/true means "no restriction";
  an explicit server-side disable always wins over the local toggle, so a
  compromised or unwanted passphrase can be shut off fleet-wide without
  physical access to the device.

A separate module rather than folded into pairing.py: unlike every other
file under /data/status, this one holds a secret, so it gets its own
tighter permissions (0o640, group-readable only) instead of the 0o644
those files use.

Whether a stream is actually playing right now lives in
srt_stream_bridge.py (in-memory — that module and this one now run in the
same process, so there's no need for a separate status file the way the
old external srt-sink-monitor.py daemon required).
"""
import json
import secrets
import string
from pathlib import Path
from urllib.parse import quote

SRT_SINK_FILE = Path("/data/status/srt-sink.json")

# Read directly by srt_stream_bridge.py (same process, same venv — no
# more cross-process duplication the way the old standalone
# srt-sink-monitor.py script needed).
SRT_PORT = 7002

# How long SRT holds packets before playout, giving itself room to
# request/receive a retransmit for anything lost — not primarily an
# end-to-end latency knob (see srt_stream_bridge.py's own fragment/
# catch-up tuning for that), it's a loss-recovery budget. 120ms wasn't
# enough on the network first tested against: ffmpeg's own stderr
# showed SRT's TSBPD logging "RCV-DROPPED N packet(s)" — packets that
# arrived, but too late to make their playout deadline, so SRT gave up
# and delivered a gap instead. That gap is what showed up client-side
# as a real decode tear, not anything in this app's own pipeline.
# Whether that headroom is actually needed is entirely a function of
# the specific network between sender and this device (wifi vs. wired,
# how congested, how far), which varies by deployment — so this is a
# per-device Settings > SRT Sink slider (see set_srt_latency_ms()) with
# 120ms as a sane starting point for a decent network, not a global
# constant anymore. A choppier network should raise it; a clean wired
# LAN can often lower it for less end-to-end latency. Applies to both
# sides of the connection (this value is shared by connect_url()'s
# mode=caller URL and srt_stream_bridge.py's mode=listener URL) — SRT
# negotiates the higher of the two peers' configured values anyway, but
# there's no reason for them to disagree here.
DEFAULT_SRT_LATENCY_MS = 120
# Sanity bounds on the Settings slider's value — wide enough to cover
# any realistic network condition without letting a typo/bad API call
# configure something SRT itself would reject or that's clearly useless
# (e.g. 0ms defeats the entire retransmit mechanism).
MIN_SRT_LATENCY_MS = 20
MAX_SRT_LATENCY_MS = 2000

PASSPHRASE_LENGTH = 10
PASSPHRASE_ALPHABET = string.ascii_letters + string.digits


def _read_raw() -> dict:
    if not SRT_SINK_FILE.exists():
        return {}
    try:
        return json.loads(SRT_SINK_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def _write_raw(data: dict) -> None:
    SRT_SINK_FILE.parent.mkdir(parents=True, exist_ok=True)
    SRT_SINK_FILE.write_text(json.dumps(data))
    SRT_SINK_FILE.chmod(0o640)


def read_config() -> dict:
    data = _read_raw()
    return {
        "local_enabled": bool(data.get("local_enabled", False)),
        "server_allows": data.get("server_allows", True) is not False,
        "passphrase": data.get("passphrase", ""),
        "debug_overlay": bool(data.get("debug_overlay", False)),
        "srt_latency_ms": _clamp_latency_ms(data.get("srt_latency_ms", DEFAULT_SRT_LATENCY_MS)),
    }


def _clamp_latency_ms(value) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SRT_LATENCY_MS
    return max(MIN_SRT_LATENCY_MS, min(MAX_SRT_LATENCY_MS, value))


def effective_enabled(config: dict | None = None) -> bool:
    """What srt_stream_bridge.py actually acts on — both the local toggle
    and the server's force-disable switch have to allow it."""
    config = config or read_config()
    return config["local_enabled"] and config["server_allows"] and bool(config["passphrase"])


def generate_passphrase() -> str:
    # secrets, not random — this is a credential, not a UI nicety.
    return "".join(secrets.choice(PASSPHRASE_ALPHABET) for _ in range(PASSPHRASE_LENGTH))


def ensure_passphrase(config: dict) -> dict:
    """Generates a passphrase the first time it's needed (first enable) —
    stable after that. Returns the config with `passphrase` guaranteed
    non-empty."""
    if not config["passphrase"]:
        config["passphrase"] = generate_passphrase()
    return config


def set_local_enabled(enabled: bool) -> dict:
    config = read_config()
    config["local_enabled"] = enabled
    if enabled:
        config = ensure_passphrase(config)
    _write_raw(config)
    return config


def regenerate_passphrase() -> dict:
    config = read_config()
    config["passphrase"] = generate_passphrase()
    _write_raw(config)
    return config


def set_srt_latency_ms(latency_ms: int) -> dict:
    """Settings > SRT Sink's latency slider — see DEFAULT_SRT_LATENCY_MS's
    own comment for why this moved from a hardcoded constant to a
    per-device setting. Read by srt_stream_bridge.py's run_forever() on
    its existing config poll, which already restarts the ffmpeg listener
    on a passphrase change — an SRT latency value can't change on a live
    connection either, so a change here triggers the same kind of
    restart (a few seconds' interruption), not a live adjustment."""
    config = read_config()
    config["srt_latency_ms"] = _clamp_latency_ms(latency_ms)
    _write_raw(config)
    return config


def set_debug_overlay(enabled: bool) -> dict:
    """Settings > SRT Sink's on-screen metrics HUD toggle — purely a
    development/tuning aid (frontend/src/srtStreamPlayer.js), not
    something an operator would normally need. Read by Slideshow.vue via
    GET /api/local/srt-sink/playing (folded in alongside `active`, since
    that's already polled every second — see main.py) rather than a
    separate endpoint, so a toggle here takes effect on the very next
    poll instead of only at the next stream start."""
    config = read_config()
    config["debug_overlay"] = enabled
    _write_raw(config)
    return config


def connect_url(hostname: str, passphrase: str, latency_ms: int) -> str:
    """The srt:// URL an external sender (OBS, vMix, ...) pastes into its
    own SRT output config to reach this device — `mode=caller` here is
    from *that* sender's point of view: this device is always the
    `mode=listener` side (see srt_stream_bridge.py), so whoever connects
    to it necessarily calls in. `latency_ms` should be this device's own
    configured value (read_config()["srt_latency_ms"]) — SRT negotiates
    the higher of the two peers' values anyway, but there's no reason
    for the sender's pasted URL to disagree with what this device itself
    is actually using."""
    return (
        f"srt://{hostname}.local:{SRT_PORT}"
        f"?mode=caller&latency={latency_ms * 1000}&passphrase={quote(passphrase)}"
    )


def set_server_allows(allows: bool) -> None:
    """Called by heartbeat.py after every heartbeat response — see that
    module's own comment on why an explicit False always overrides the
    local toggle, and why anything else (True, or the key simply absent
    from an older/unmodified server's response) does not restrict it."""
    config = read_config()
    if config["server_allows"] == allows:
        return
    config["server_allows"] = allows
    _write_raw(config)
