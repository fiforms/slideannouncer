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
import functools
import ipaddress
import json
import secrets
import string
import subprocess
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
# libsrt's own passphrase rule — also applied to RIST Unicast, which shares
# the same (generated, now also hand-editable) passphrase.
MIN_PASSPHRASE_LENGTH = 10
MAX_PASSPHRASE_LENGTH = 79

# --- Receiver modes ----------------------------------------------------
# "srt"            — SRT listener on SRT_PORT (the original mode). Senders
#                    call in to this device.
# "rist_unicast"   — RIST (main profile) listener on RIST_PORT, encrypted
#                    with the same passphrase as SRT mode.
# "rist_multicast" — joins a RIST multicast group the sender transmits to.
#                    SRT itself has no multicast mode at all, which is why
#                    multicast is RIST-only. Group/port/passphrase are
#                    whatever the sender is configured with, so they're
#                    entered by hand and must match it exactly.
# All three feed the same srt_stream_bridge.py remux pipeline — only
# ffmpeg's input side differs (see listener_input_args()).
MODES = ("srt", "rist_unicast", "rist_multicast")
DEFAULT_MODE = "srt"
# RIST main profile sends media on an even UDP port and its RTCP
# (retransmit requests) on port+1, so ports are kept even.
RIST_PORT = 5000
DEFAULT_MULTICAST_PORT = 5000
# RIST's receive buffer: how long it holds packets to wait for
# retransmits of lost ones — the RIST equivalent of the SRT latency
# slider. librist's own default is 1000ms (sized for the internet); a LAN
# needs far less, so 500ms is the starting point.
DEFAULT_RIST_BUFFER_MS = 500
MIN_RIST_BUFFER_MS = 50
MAX_RIST_BUFFER_MS = 3000
# AES key size — the sender has to use the same one as the passphrase.
RIST_ENCRYPTION_BITS = (128, 256)
DEFAULT_RIST_ENCRYPTION_BITS = 128


# Settings the AnnouncementSlides web UI (Slide Announcer page) can also
# set. Both sides can edit them, so they sync with a revision counter
# rather than either side simply overwriting the other:
# - every heartbeat reports this device's current values plus
#   `server_revision`, the last web revision it applied (report());
# - a web edit bumps the server's revision, and the heartbeat and slide
#   sync responses carry it down; apply_server_config() applies anything
#   newer than server_revision, then records it;
# - a local edit here just changes the values, and the next heartbeat
#   reports them — the server takes a report as current whenever it
#   comes with the server's latest revision.
SERVER_EDITABLE_FIELDS = (
    "mode", "passphrase", "multicast_group", "multicast_port", "multicast_passphrase", "rist_encryption_bits",
)


class SrtSinkConfigError(ValueError):
    """Raised with a message safe to show directly on the Settings screen."""


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
    mode = data.get("mode")
    encryption = data.get("rist_encryption_bits")
    return {
        "local_enabled": bool(data.get("local_enabled", False)),
        "server_allows": data.get("server_allows", True) is not False,
        "mode": mode if mode in MODES else DEFAULT_MODE,
        "passphrase": data.get("passphrase", ""),
        "debug_overlay": bool(data.get("debug_overlay", False)),
        "srt_latency_ms": _clamp_latency_ms(data.get("srt_latency_ms", DEFAULT_SRT_LATENCY_MS)),
        "rist_buffer_ms": _clamp(data.get("rist_buffer_ms"), MIN_RIST_BUFFER_MS, MAX_RIST_BUFFER_MS, DEFAULT_RIST_BUFFER_MS),
        "rist_encryption_bits": encryption if encryption in RIST_ENCRYPTION_BITS else DEFAULT_RIST_ENCRYPTION_BITS,
        "multicast_group": data.get("multicast_group", ""),
        "multicast_port": _clamp(data.get("multicast_port"), 1024, 65534, DEFAULT_MULTICAST_PORT),
        "multicast_passphrase": data.get("multicast_passphrase", ""),
        "server_revision": _clamp(data.get("server_revision"), 0, 2**31, 0),
        "server_apply_error": data.get("server_apply_error"),
    }


def _clamp(value, low: int, high: int, default: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _clamp_latency_ms(value) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SRT_LATENCY_MS
    return max(MIN_SRT_LATENCY_MS, min(MAX_SRT_LATENCY_MS, value))


def effective_enabled(config: dict | None = None) -> bool:
    """What srt_stream_bridge.py actually acts on — both the local toggle
    and the server's force-disable switch have to allow it, and the
    selected mode has to be fully configured (a multicast group and the
    sender's passphrase entered; RIST modes also need RIST support in
    this device's ffmpeg)."""
    config = config or read_config()
    if not (config["local_enabled"] and config["server_allows"]):
        return False
    if config["mode"] == "rist_multicast":
        return bool(config["multicast_group"] and config["multicast_passphrase"]) and rist_supported()
    if config["mode"] == "rist_unicast":
        return bool(config["passphrase"]) and rist_supported()
    return bool(config["passphrase"])


@functools.cache
def rist_supported() -> bool:
    """Whether this device's ffmpeg was built with librist (`rist` in
    `ffmpeg -protocols`). Checked once per process — the ffmpeg binary
    only changes with an OS update, which restarts this backend anyway."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-protocols"], capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(line.strip() == "rist" for line in out.splitlines())


def listener_input_args(config: dict) -> list[str]:
    """ffmpeg's input-side arguments (protocol options + `-i <url>`) for
    the configured mode — everything after this is the same remux
    pipeline in every mode (see srt_stream_bridge.py's _ffmpeg_cmd())."""
    mode = config["mode"]
    if mode == "srt":
        return [
            "-i",
            f"srt://0.0.0.0:{SRT_PORT}?mode=listener"
            f"&passphrase={quote(config['passphrase'])}&latency={config['srt_latency_ms'] * 1000}",
        ]
    if mode == "rist_unicast":
        secret, url = config["passphrase"], f"rist://@0.0.0.0:{RIST_PORT}"
    else:
        # A multicast address after the "@" (listen) makes librist join
        # that group rather than bind a plain unicast port.
        secret = config["multicast_passphrase"]
        url = f"rist://@{config['multicast_group']}:{config['multicast_port']}"
    return [
        "-rist_profile", "main",
        "-buffer_size", str(config["rist_buffer_ms"]),
        "-secret", secret,
        "-encryption", str(config["rist_encryption_bits"]),
        "-i", url,
    ]


def update_settings(changes: dict) -> dict:
    """Settings > LAN Video Receiver's mode and RIST fields. Every field is
    optional; each one given is validated before anything is written, so
    a bad value never half-applies. Raises SrtSinkConfigError."""
    config = read_config()
    if "mode" in changes:
        if changes["mode"] not in MODES:
            raise SrtSinkConfigError(f"Unknown mode: {changes['mode']}")
        if changes["mode"] != "srt" and not rist_supported():
            raise SrtSinkConfigError("This device's ffmpeg was built without RIST support.")
        config["mode"] = changes["mode"]
    if "passphrase" in changes:
        config["passphrase"] = _validate_passphrase(changes["passphrase"])
    if "multicast_passphrase" in changes:
        config["multicast_passphrase"] = _validate_passphrase(changes["multicast_passphrase"])
    if "multicast_group" in changes:
        config["multicast_group"] = _validate_multicast_group(changes["multicast_group"])
    if "multicast_port" in changes:
        config["multicast_port"] = _validate_rist_port(changes["multicast_port"])
    if "rist_buffer_ms" in changes:
        config["rist_buffer_ms"] = _clamp(changes["rist_buffer_ms"], MIN_RIST_BUFFER_MS, MAX_RIST_BUFFER_MS, DEFAULT_RIST_BUFFER_MS)
    if "rist_encryption_bits" in changes:
        if changes["rist_encryption_bits"] not in RIST_ENCRYPTION_BITS:
            raise SrtSinkConfigError("Encryption must be AES-128 or AES-256.")
        config["rist_encryption_bits"] = changes["rist_encryption_bits"]
    _write_raw(config)
    return config


def apply_server_config(push) -> None:
    """Applies a web-side edit pushed down in a heartbeat or slide-sync
    response ({"revision": n, <SERVER_EDITABLE_FIELDS>...}), if it's newer
    than the last one applied. Validated exactly like a local edit; one
    that fails (e.g. RIST chosen on a device whose ffmpeg lacks it) is
    still marked applied so it isn't retried every sync, and its error is
    reported back up for the web page to show."""
    if not isinstance(push, dict):
        return
    try:
        revision = int(push.get("revision") or 0)
    except (TypeError, ValueError):
        return
    if revision <= read_config()["server_revision"]:
        return
    changes = {key: push[key] for key in SERVER_EDITABLE_FIELDS if push.get(key) not in (None, "")}
    error = None
    try:
        update_settings(changes)
    except SrtSinkConfigError as exc:
        error = str(exc)
    config = read_config()
    config["server_revision"] = revision
    config["server_apply_error"] = error
    _write_raw(config)


def report() -> dict:
    """This device's receiver settings for the heartbeat (see
    SERVER_EDITABLE_FIELDS for how they sync), plus read-only status the
    web page shows alongside them."""
    config = read_config()
    return {
        **{key: config[key] for key in SERVER_EDITABLE_FIELDS},
        "local_enabled": config["local_enabled"],
        "srt_latency_ms": config["srt_latency_ms"],
        "rist_buffer_ms": config["rist_buffer_ms"],
        "rist_supported": rist_supported(),
        "rist_port": RIST_PORT,
        "srt_port": SRT_PORT,
        "apply_error": config["server_apply_error"],
        "revision": config["server_revision"],
    }


def _validate_passphrase(value) -> str:
    value = (value or "").strip()
    if not (MIN_PASSPHRASE_LENGTH <= len(value) <= MAX_PASSPHRASE_LENGTH):
        raise SrtSinkConfigError(
            f"Passphrase must be {MIN_PASSPHRASE_LENGTH}–{MAX_PASSPHRASE_LENGTH} characters."
        )
    if not value.isascii() or not value.isprintable() or " " in value:
        raise SrtSinkConfigError("Passphrase can only use letters, numbers and symbols (no spaces).")
    return value


def _validate_multicast_group(value) -> str:
    value = (value or "").strip()
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        raise SrtSinkConfigError(f"{value or 'That'} isn't a valid IPv4 address.") from None
    # 224.0.0.0/24 is reserved for routing protocols (never used for media).
    if not address.is_multicast or address in ipaddress.IPv4Network("224.0.0.0/24"):
        raise SrtSinkConfigError("Multicast address must be in 224.0.1.0–239.255.255.255 (e.g. 239.1.2.3).")
    return str(address)


def _validate_rist_port(value) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise SrtSinkConfigError("Port must be a number.") from None
    if not (1024 <= port <= 65534) or port % 2:
        raise SrtSinkConfigError("Port must be an even number from 1024 to 65534 (RIST uses the next port up too).")
    return port


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


def sender_url(hostname: str, config: dict) -> str | None:
    """The URL to configure a sender (OBS, vMix, an encoder) with for the
    current mode — shown on screen and as the QR code. In multicast mode
    it's the group the sender transmits to, not this device's address."""
    mode = config["mode"]
    if mode == "srt":
        return connect_url(hostname, config["passphrase"], config["srt_latency_ms"]) if config["passphrase"] else None
    if mode == "rist_unicast":
        host, port, secret = f"{hostname}.local", RIST_PORT, config["passphrase"]
    else:
        host, port, secret = config["multicast_group"], config["multicast_port"], config["multicast_passphrase"]
    if not (host and secret):
        return None
    return (
        f"rist://{host}:{port}?secret={quote(secret)}"
        f"&aes-type={config['rist_encryption_bits']}&buffer={config['rist_buffer_ms']}"
    )


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
