"""Device identity helpers shared by pairing.py and heartbeat.py.

The tamper-resistant half of a device's identity (identity.key, the HMAC
check) is provisioning.py's concern (runs as root, once per boot, before
this backend ever starts) — see SLIDE_ANNOUNCER.md, "Device identity &
anti-clone protection." This module only *reads* the already-verified
device_uuid firstboot.py wrote to /data/status/setup-mode.json, plus the
same MAC-detection logic firstboot.py uses for its own check, so the
pairing request and the heartbeat both report the identity firstboot.py
already vouched for on this boot, without re-implementing the HMAC check
here.
"""
import hashlib
import json
from pathlib import Path

SETUP_MODE_STATUS = Path("/data/status/setup-mode.json")


def get_device_uuid() -> str | None:
    if not SETUP_MODE_STATUS.exists():
        return None
    try:
        return json.loads(SETUP_MODE_STATUS.read_text()).get("device_uuid")
    except json.JSONDecodeError:
        return None


def get_mac_address() -> str | None:
    """Mirrors firstboot.py's get_mac_address(), but returns None instead
    of raising — this is a best-effort report field here, not an identity
    check with a wipe-and-reboot consequence.
    """
    net_dir = Path("/sys/class/net")
    if not net_dir.exists():
        return None

    candidates = sorted(
        (p for p in net_dir.iterdir() if p.name != "lo"),
        key=lambda p: (not p.name.startswith(("eth", "en")), p.name),
    )
    for iface in candidates:
        addr_file = iface / "address"
        if addr_file.exists():
            mac = addr_file.read_text().strip()
            if mac and mac != "00:00:00:00:00:00":
                return mac.lower()
    return None


def derive_numeric_hostname(device_uuid: str) -> str:
    """Mirrors firstboot.py's derive_numeric_hostname() — a stable 6-digit
    numeric suffix derived from device_uuid. pairing.py's only use for this
    is the rare fallback where a typed device name slugifies to nothing
    (e.g. it's pure emoji/punctuation); the normal case is a name-derived
    hostname, not this.
    """
    digest = hashlib.sha256(device_uuid.encode()).hexdigest()
    return f"slideannouncer-{int(digest, 16) % 1_000_000:06d}"
