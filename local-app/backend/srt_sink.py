"""On-demand SRT video-sink configuration — enable toggle + passphrase for
system/scripts/srt-sink-monitor.py, backing Settings > SRT Sink. Persisted
at /data/status/srt-sink.json, the same flat-file-under-/data/status
pattern pairing.py uses for audio output/volume — the daemon (its own
root-owned unit, see system/slide-announcer-srt-sink.service, running as
the `slideannouncer` user) reads this file directly, no API round-trip
needed on its side.

The passphrase is never operator-typed: it's generated once, on-device,
the first time SRT Sink is enabled (ensure_passphrase()), and reported up
to the server on every heartbeat (see heartbeat.py's payload) purely so an
admin can read it off the fleet dashboard to configure their SRT sender —
the server never sets it, only mirrors it.

Two independent "should this run" inputs, both must be true for the
daemon to actually listen — see effective_enabled():
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
"""
import json
import secrets
import string
from pathlib import Path
from urllib.parse import quote

SRT_SINK_FILE = Path("/data/status/srt-sink.json")

# Must match system/scripts/srt-sink-monitor.py's SRT_PORT — kept here too
# (rather than importing across the venv/system-script boundary) purely so
# main.py can hand the Settings UI a real "Connect With" srt:// URL without
# hardcoding the port a second time.
SRT_PORT = 7002

# SRT's own recommended receive-buffer latency for a caller connecting
# over a typical home/church LAN — high enough to ride out ordinary wifi
# jitter without the sender needing to tune anything itself. Microseconds,
# per SRT's own `latency` URL parameter.
SRT_LATENCY_MICROSECONDS = 120_000

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
    }


def effective_enabled(config: dict | None = None) -> bool:
    """What system/scripts/srt-sink-monitor.py actually acts on — both the
    local toggle and the server's force-disable switch have to allow it."""
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


def connect_url(hostname: str, passphrase: str) -> str:
    """The srt:// URL an external sender (OBS, vMix, ...) pastes into its
    own SRT output config to reach this device — `mode=caller` here is
    from *that* sender's point of view: this device is always the
    `mode=listener` side (see srt-sink-monitor.py), so whoever connects to
    it necessarily calls in."""
    return (
        f"srt://{hostname}.local:{SRT_PORT}"
        f"?mode=caller&latency={SRT_LATENCY_MICROSECONDS}&passphrase={quote(passphrase)}"
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
