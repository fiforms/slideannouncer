#!/usr/bin/env python3
"""Automatic Tier 1 (OS) update installer — see SLIDE_ANNOUNCER.md, Tier 1
"Device-side update flow" and "Update safety". Runs as its own root oneshot
unit (slide-announcer-os-updater.service, fired by its matching .timer),
the automated counterpart to slide-announcer-update's manual
check/install/tryboot subcommands (system/scripts/rauc-update.py) — this
script wraps the exact same `rauc` calls, just without a human at the
console deciding when.

Reads update-availability from heartbeat.py's own cached status file
(/data/status/heartbeat.json), the same way updater/local_app_updater.py
does for the app tier — no extra network round trip. The server already
does the hard part of ordering hotfixes ahead of full images:
SlideAnnouncerRelease::resolveForDevice() only ever offers a hotfix when
its required_base_version exactly matches the device's current os_version,
falling back to the tagged full release otherwise — so "apply hotfixes
before a full-image upgrade" falls out of "always install whatever the
server currently resolves," one hop per heartbeat, with no extra ordering
logic needed here. This script's own ordering responsibility is narrower:
never start an OS-level change while a local-app update is still pending,
so the two tiers don't restart/reboot the device in the same maintenance
window (see SLIDE_ANNOUNCER.md's "Cross-tier update safety").

Hotfix vs full changes what happens after `rauc install` succeeds — see
os_release_type in the heartbeat response (SlideAnnouncerHeartbeatController):
- 'hotfix' targets the live-root slot (system/rauc/system.conf's
  [slot.hotfix.0]) directly, no A/B, no reboot required by RAUC itself —
  the bundle's own optional script.sh (see make-hotfix-bundle.sh) is
  responsible for restarting anything it touched. This script does nothing
  further.
- 'full' targets the A/B rootfs/kernel slots and needs the
  install -> tryboot reboot -> health check -> commit cycle
  (slide-announcer-tryboot-check.service/rpi-tryboot-commit.sh, already
  built and hardware-validated) to actually take effect and be verified.
  This script's job ends at triggering the tryboot reboot; everything
  after that already exists.
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HEARTBEAT_STATUS_FILE = Path("/data/status/heartbeat.json")
UPDATER_STATUS_FILE = Path("/data/status/os-updater.json")
VERSION_FILE = Path("/opt/slide-announcer/VERSION")

IDLE_WINDOW_START_HOUR = 2  # 02:00 local — same window as the app-tier updater
IDLE_WINDOW_END_HOUR = 5  # 05:00 local


def log(msg: str) -> None:
    print(f"os-updater: {msg}", flush=True)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _write_status(data: dict) -> None:
    UPDATER_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = UPDATER_STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.chmod(0o644)
    tmp.replace(UPDATER_STATUS_FILE)


def _read_status() -> dict:
    if not UPDATER_STATUS_FILE.exists():
        return {}
    try:
        return json.loads(UPDATER_STATUS_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def within_idle_window(now: datetime | None = None) -> bool:
    hour = (now or datetime.now()).hour
    if IDLE_WINDOW_START_HOUR <= IDLE_WINDOW_END_HOUR:
        return IDLE_WINDOW_START_HOUR <= hour < IDLE_WINDOW_END_HOUR
    return hour >= IDLE_WINDOW_START_HOUR or hour < IDLE_WINDOW_END_HOUR


def read_heartbeat_update_info() -> dict | None:
    if not HEARTBEAT_STATUS_FILE.exists():
        return None
    try:
        status = json.loads(HEARTBEAT_STATUS_FILE.read_text())
    except json.JSONDecodeError:
        return None
    return status.get("response")


def installed_version() -> str | None:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return None


def rauc_install(bundle_url: str) -> bool:
    log(f"rauc install {bundle_url}")
    result = subprocess.run(["rauc", "install", bundle_url])
    return result.returncode == 0


def start_tryboot() -> bool:
    # --no-block: slide-announcer-tryboot.service's own ExecStart reboots
    # the machine, so a blocking `systemctl start` can have its job
    # canceled by the shutdown before it reports back — see
    # rauc-update.py's cmd_tryboot for the same reasoning.
    result = subprocess.run(["systemctl", "start", "--no-block", "slide-announcer-tryboot.service"])
    return result.returncode == 0


def main() -> int:
    info = read_heartbeat_update_info()
    if not info:
        log("no heartbeat status yet — nothing to check against")
        return 0

    if not info.get("os_update_available"):
        log("no OS update available")
        return 0

    if not info.get("os_auto_update_enabled", True):
        log("os_auto_update_enabled is false for this device — leaving it for manual install")
        return 0

    # Ordering: never let an OS-level change (a reboot either way) land in
    # the same window as a pending local-app update — see this file's
    # docstring and SLIDE_ANNOUNCER.md's "Cross-tier update safety."
    if info.get("app_update_available"):
        log("a local-app update is still pending — deferring the OS update until that's applied")
        return 0

    version = info.get("latest_os_version")
    bundle_url = info.get("os_bundle_url")
    release_type = info.get("os_release_type")
    if not version or not bundle_url or release_type not in ("hotfix", "full"):
        log("os_update_available but missing latest_os_version/os_bundle_url/os_release_type — skipping")
        return 0

    if version == installed_version():
        log(f"already on {version}")
        return 0

    status = _read_status()
    if status.get("attempted_version") == version and status.get("result") == "install_failed":
        log(f"already attempted {version} and the install failed — "
            "waiting for a newer release rather than retrying every cycle")
        return 0

    if not within_idle_window():
        log(f"{release_type} update to {version} available but outside the idle window "
            f"({IDLE_WINDOW_START_HOUR:02d}:00-{IDLE_WINDOW_END_HOUR:02d}:00 local) — deferring")
        return 0

    if not rauc_install(bundle_url):
        log(f"rauc install failed for {version} ({release_type})")
        _write_status({
            "checked_at": _now_iso(), "attempted_version": version,
            "release_type": release_type, "result": "install_failed",
        })
        return 1

    if release_type == "hotfix":
        # Live-rootfs write already took effect; VERSION is already bumped
        # by the bundle's own hook. No reboot to trigger — see this file's
        # docstring.
        log(f"hotfix {version} installed")
        _write_status({
            "checked_at": _now_iso(), "attempted_version": version,
            "release_type": "hotfix", "result": "installed",
        })
        return 0

    # 'full': staged onto the inactive A/B slot — nothing is live yet.
    # slide-announcer-tryboot-check.service handles the health-check/
    # commit/rollback on the far side of this reboot; this script's job
    # ends here.
    log(f"full image {version} staged — triggering tryboot")
    if not start_tryboot():
        log("failed to start slide-announcer-tryboot.service")
        _write_status({
            "checked_at": _now_iso(), "attempted_version": version,
            "release_type": "full", "result": "tryboot_trigger_failed",
        })
        return 1

    _write_status({
        "checked_at": _now_iso(), "attempted_version": version,
        "release_type": "full", "result": "tryboot_triggered",
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
