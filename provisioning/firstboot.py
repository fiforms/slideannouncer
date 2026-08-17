#!/usr/bin/env python3
"""Slide Announcer first-boot / every-boot provisioning.

Run by slide-announcer-firstboot.service, after slide-announcer-data-resize
has grown /data. Everything here is self-contained filesystem/crypto/local
NetworkManager-query work — no calls to the AnnouncementSlides server
itself, no dependency on the pairing/sync API.

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
import time
import uuid
from pathlib import Path

import yaml

BOOT_YAML = Path("/boot/firmware/slideannouncer.yaml")
NETWORK_CONFIG = Path("/boot/firmware/network-config")
DATA_DIR = Path("/data")
IDENTITY_KEY_PATH = Path("/data/identity.key")
FIRSTBOOT_MARKER = Path("/data/.firstboot-complete")
STATUS_DIR = Path("/data/status")
SETUP_MODE_STATUS = STATUS_DIR / "setup-mode.json"
LANGUAGE_BOOT_HINT_STATUS = STATUS_DIR / "language-boot-hint.json"
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
    # /boot/firmware is ro by default (see 00-run.sh's fstab entry) —
    # bracket this write the same way every other writer of this partition
    # does (rpi-tryboot-backend.sh, rpi-tryboot-commit.sh,
    # factory-reset-check.sh, slide-announcer-factory-reset-trigger.service).
    # try/finally so the remount back to ro still happens even if the write
    # itself fails partway.
    subprocess.run(["slide-announcer-bootfw-remount", "rw"], check=True)
    try:
        BOOT_YAML.write_text(yaml.safe_dump(data, sort_keys=False))
    finally:
        subprocess.run(["slide-announcer-bootfw-remount", "ro"], check=True)


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


def _network_connected_now() -> bool:
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "TYPE,STATE", "device", "status"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

    for line in out.splitlines():
        fields = line.split(":")
        if len(fields) == 2 and fields[0] in ("wifi", "ethernet") and fields[1] == "connected":
            return True
    return False


def _network_preconfigured() -> bool:
    """True if network-config (cloud-init's NoCloud netplan file, also on
    the boot partition) actually declares a network to apply — the shipped
    default ships entirely commented out, so this is normally False.
    """
    if not NETWORK_CONFIG.exists():
        return False
    try:
        data = yaml.safe_load(NETWORK_CONFIG.read_text())
    except yaml.YAMLError:
        return False
    return bool((data or {}).get("network"))


def has_network_connectivity(timeout: float = 15.0, poll_interval: float = 2.0) -> bool:
    """True once NetworkManager reports an active wifi/ethernet connection —
    e.g. pre-provisioned via cloud-init's network-config (see
    docs/BUILDING.md, "Pre-provisioning network + identity"), applied
    entirely outside this project's own code, so there's nothing to
    inspect here except the result.

    Only actually polls (up to `timeout`) if network-config declares
    something to apply: this service only orders
    After=network-pre.target, which runs BEFORE networking is configured,
    not after it's up, so WiFi association + DHCP can easily take longer
    than an instantaneous check to settle on a device that's about to
    connect fine. But slide-announcer-kiosk.service Requires= this
    service, so blindly waiting out that same timeout on a device with
    nothing pre-provisioned at all (the common case — HID setup or
    AP-mode fallback instead) would just be a pure delay before the kiosk
    display can ever appear, for a connection that was never coming. An
    already-plugged-in ethernet cable is covered by the immediate check
    below regardless — wired connections don't have WiFi's
    association/DHCP delay to race against.
    """
    if _network_connected_now():
        return True
    if not _network_preconfigured():
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        if _network_connected_now():
            return True
    return False


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

    if has_network_connectivity():
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


def write_language_boot_hint() -> None:
    """Writes the boot-yaml `language` key (e.g. "es") to a status file the
    backend reads as a pre-pairing default — see pairing.py's
    `read_effective_language()`. This is only ever a *hint*: once the
    device pairs, the server-assigned language is authoritative and this
    file is never consulted again while paired (same precedence as
    device_name/entity_name). Runs every boot, not just first boot, so
    editing the boot partition's language key and rebooting an unpaired
    device picks up the change.
    """
    config = load_boot_config()
    ensure_status_dir()
    LANGUAGE_BOOT_HINT_STATUS.write_text(json.dumps({"code": config.get("language")}))
    # Same rationale as SETUP_MODE_STATUS.chmod() below — this script runs
    # as root, the backend that reads it back does not.
    LANGUAGE_BOOT_HINT_STATUS.chmod(0o644)


def main() -> int:
    run_once_setup()
    ensure_identity()
    detect_setup_mode()
    write_language_boot_hint()
    return 0


if __name__ == "__main__":
    sys.exit(main())
