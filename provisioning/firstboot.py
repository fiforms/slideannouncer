#!/usr/bin/env python3
"""Slide Announcer first-boot / every-boot provisioning.

Run by slide-announcer-firstboot.service, after slide-announcer-data-resize
has grown /data. Everything here is self-contained filesystem/crypto work —
no network calls, no dependency on the (not yet built) pairing/sync API.

See SLIDE_ANNOUNCER.md, "Device identity & anti-clone protection" and
"First-boot / WiFi setup flow" for the full design this implements.
"""
import grp
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import yaml

BOOT_YAML = Path("/boot/firmware/slideannouncer.yaml")
DATA_DIR = Path("/data")
IDENTITY_KEY_PATH = Path("/data/identity.key")
FIRSTBOOT_MARKER = Path("/data/.firstboot-complete")
STATUS_DIR = Path("/data/status")
SETUP_MODE_STATUS = STATUS_DIR / "setup-mode.json"
BACKEND_GROUP = "slideannouncer"

# Paths a real wipe-and-repair (per Heartbeat/Kiosk-display design) would
# clear. None of these exist yet in the stub app, but clearing them here too
# is harmless and keeps this script correct once Tier 2 lands.
WIPE_ON_IDENTITY_MISMATCH = [
    Path("/data/device-token"),
    Path("/data/slides"),
    Path("/data/local-app/settings.json"),
]


def log(msg: str) -> None:
    print(f"firstboot: {msg}", flush=True)


def ensure_data_group_writable() -> None:
    """/data is created by mkfs (root:root, mode 0755) — the ext4 default
    grants "other" read+traverse only, not write. The local-app backend
    (system/slide-announcer-backend.service) runs as the unprivileged
    `slideannouncer` user, and now needs to create new files directly
    under /data (device-token) and /data/status (heartbeat.json) — see
    pairing.py/heartbeat.py. Group-owning /data by that user's group and
    making it group-writable is the minimal fix; called from
    run_once_setup() so it reruns exactly when /data does (a factory
    reset reformats /data, which wipes FIRSTBOOT_MARKER right along with
    it, re-triggering this on the very next boot).
    """
    gid = grp.getgrnam(BACKEND_GROUP).gr_gid
    os.chown(DATA_DIR, 0, gid)
    DATA_DIR.chmod(0o775)


def ensure_status_dir() -> None:
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    gid = grp.getgrnam(BACKEND_GROUP).gr_gid
    os.chown(STATUS_DIR, 0, gid)
    STATUS_DIR.chmod(0o775)


def run_once_setup() -> None:
    """SSH host keys + machine-id: regenerate exactly once, ever."""
    if FIRSTBOOT_MARKER.exists():
        return

    log("first boot detected — regenerating SSH host keys and machine-id")

    for key in Path("/etc/ssh").glob("ssh_host_*"):
        key.unlink()
    subprocess.run(["ssh-keygen", "-A"], check=True)

    Path("/etc/machine-id").write_text("")
    dbus_machine_id = Path("/var/lib/dbus/machine-id")
    if dbus_machine_id.exists() or dbus_machine_id.is_symlink():
        dbus_machine_id.unlink()
    subprocess.run(["systemd-machine-id-setup"], check=True)
    dbus_machine_id.symlink_to("/etc/machine-id")

    ensure_data_group_writable()
    ensure_status_dir()
    FIRSTBOOT_MARKER.touch()
    log("first-boot setup complete")


def get_mac_address() -> str:
    """MAC of the first usable non-loopback interface (eth0/wlan0 preferred)."""
    net_dir = Path("/sys/class/net")
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
    raise RuntimeError("no usable network interface found for identity check")


def load_boot_config() -> dict:
    if not BOOT_YAML.exists():
        return {}
    data = yaml.safe_load(BOOT_YAML.read_text())
    return data or {}


def save_boot_config(data: dict) -> None:
    BOOT_YAML.write_text(yaml.safe_dump(data, sort_keys=False))


def compute_check(identity_key: bytes, device_uuid: str, mac: str) -> str:
    return hmac.new(identity_key, (device_uuid + mac).encode(), hashlib.sha256).hexdigest()


def ensure_identity() -> None:
    """HMAC-SHA256(identity_key, device_uuid + mac) consistency check.

    Match -> proceed. Mismatch (missing/invalid identity_key, missing
    declared UUID, hand-edited UUID, hardware swap, or a cloned SD card) ->
    regenerate a fresh device_uuid + identity_key pair and wipe local state.
    """
    mac = get_mac_address()
    config = load_boot_config()

    identity_key = IDENTITY_KEY_PATH.read_bytes() if IDENTITY_KEY_PATH.exists() else None
    declared_uuid = config.get("device_uuid")
    declared_check = config.get("device_uuid_check")

    consistent = (
        identity_key is not None
        and declared_uuid
        and declared_check
        and hmac.compare_digest(compute_check(identity_key, declared_uuid, mac), declared_check)
    )

    if consistent:
        log(f"identity OK (device_uuid={declared_uuid})")
        return

    log("identity missing or inconsistent — regenerating device_uuid + identity_key")
    for path in WIPE_ON_IDENTITY_MISMATCH:
        if path.is_dir():
            import shutil

            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()

    # os.urandom, not Path("/dev/urandom").read_bytes() — that device never
    # hits EOF, so .read_bytes() (which reads until EOF) spins forever.
    new_identity_key = os.urandom(32)
    IDENTITY_KEY_PATH.write_bytes(new_identity_key)
    IDENTITY_KEY_PATH.chmod(0o600)

    new_uuid = str(uuid.uuid4())
    new_check = compute_check(new_identity_key, new_uuid, mac)
    config["device_uuid"] = new_uuid
    config["device_uuid_check"] = new_check
    save_boot_config(config)
    log(f"new identity written (device_uuid={new_uuid})")


def has_wifi_credentials(config: dict) -> bool:
    wifi = config.get("wifi") or {}
    return bool(wifi.get("ssid")) and wifi.get("password") is not None


def detect_hid_input() -> bool:
    """Probe for an attached keyboard + relative-pointer HID device."""
    try:
        out = subprocess.run(
            ["libinput", "list-devices"], capture_output=True, text=True, timeout=5
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

    has_keyboard = False
    has_pointer = False
    for block in out.split("\n\n"):
        caps_match = re.search(r"^Capabilities:\s*(.+)$", block, re.MULTILINE)
        if not caps_match:
            continue
        caps = caps_match.group(1)
        if "keyboard" in caps:
            has_keyboard = True
        if "pointer" in caps:
            has_pointer = True

    return has_keyboard and has_pointer


def detect_setup_mode() -> None:
    """Senses which of the three setup modalities applies. Does not act on
    it — acting (joining WiFi, launching the setup UI) is Tier 2 (the real
    local-app), not implemented here.
    """
    config = load_boot_config()

    if has_wifi_credentials(config):
        mode = "headless-config"
    elif detect_hid_input():
        mode = "hid-setup"
    else:
        mode = "ap-mode-fallback"

    log(f"detected setup mode: {mode}")
    ensure_status_dir()
    SETUP_MODE_STATUS.write_text(
        json.dumps({"setup_mode": mode, "device_uuid": config.get("device_uuid")}, indent=2)
    )
    # This service (and update-check.py's own status file) run as root;
    # local-app's backend, which reads this back, doesn't — see
    # local-app-seed.py's identical fix for why an explicit chmod (not just
    # whatever this service's umask happens to produce) is needed here.
    SETUP_MODE_STATUS.chmod(0o644)


def main() -> int:
    run_once_setup()
    ensure_identity()
    detect_setup_mode()
    return 0


if __name__ == "__main__":
    sys.exit(main())
