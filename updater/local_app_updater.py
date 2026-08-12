#!/usr/bin/env python3
"""Local-app self-updater (Tier 2) — see SLIDE_ANNOUNCER.md, "Tier 2 — Local
web app", "Update mechanism". Runs as a standalone script fired periodically
by systemd (slide-announcer-local-app-updater.service, via its matching
.timer), not an asyncio task inside the backend — the backend restarting
itself mid-update would kill the very process running the update, so this
deliberately lives outside it, the same way slide-announcer-update-check.py
(the Tier 1/OS check triggered from the Settings UI) already runs as its
own process rather than inside the request-serving backend.

Deliberately simpler than the Tier 1 (RAUC) OS OTA: no A/B slots, since a
bad app update can't brick the device the way a bad OS update can. An
atomic symlink swap (`/data/local-app/current`) plus a post-restart health
check with auto-revert covers the same "never leave the device dead" goal
with far less infrastructure. This script owns /data/local-app itself and
runs as the `slideannouncer` user (systemd unit's User=), the same account
local-app-seed.py hands ownership of /data/local-app to at boot — no root
needed, other than the two narrow systemctl actions already granted to
that user by system/polkit/50-slide-announcer-system.rules (restarting
slide-announcer-*.service units).

Reads update-availability from heartbeat.py's own status file rather than
making a second network call — heartbeat.py already polls the server every
5 minutes and its cached response already carries
app_update_available/app_download_url/app_sha256/latest_app_version (see
SLIDE_ANNOUNCER.md, "Heartbeat + version checks"). Version *checks* are
therefore effectively continuous, bounded only by heartbeat's own cadence;
only the download+swap+restart ("apply") is gated to the idle window below,
since restarting the backend causes a brief kiosk hiccup.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path

import httpx

HEARTBEAT_STATUS_FILE = Path("/data/status/heartbeat.json")
UPDATER_STATUS_FILE = Path("/data/status/local-app-updater.json")
DATA_LOCAL_APP = Path("/data/local-app")
CURRENT_LINK = DATA_LOCAL_APP / "current"
RELEASES_DIR = DATA_LOCAL_APP / "releases"

KEEP_RELEASES = 3
IDLE_WINDOW_START_HOUR = 2  # 02:00 local
IDLE_WINDOW_END_HOUR = 5  # 05:00 local
HEALTH_CHECK_TIMEOUT_SECONDS = 30
HEALTH_CHECK_INTERVAL_SECONDS = 2
BACKEND_STATUS_URL = "http://127.0.0.1:8000/api/local/status"


def log(msg: str) -> None:
    print(f"local-app-updater: {msg}", flush=True)


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
    # Wraps past midnight (e.g. 22-5) — not used by the defaults above, but
    # kept correct in case the window is ever reconfigured that way.
    return hour >= IDLE_WINDOW_START_HOUR or hour < IDLE_WINDOW_END_HOUR


def read_heartbeat_update_info() -> dict | None:
    """The server's last heartbeat response, as cached by heartbeat.py —
    see SlideAnnouncerHeartbeatController for the field shapes."""
    if not HEARTBEAT_STATUS_FILE.exists():
        return None
    try:
        status = json.loads(HEARTBEAT_STATUS_FILE.read_text())
    except json.JSONDecodeError:
        return None
    return status.get("response")


def installed_version() -> str | None:
    version_file = CURRENT_LINK / "VERSION"
    if not version_file.exists():
        return None
    return version_file.read_text().strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, dest: Path, expected_sha256: str | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with httpx.stream("GET", url, timeout=60, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)
    if expected_sha256 and _sha256(tmp) != expected_sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"sha256 mismatch downloading {url}")
    tmp.replace(dest)


def extract_and_smoke_check(archive: Path, version: str) -> Path:
    target_dir = RELEASES_DIR / version
    tmp_dir = RELEASES_DIR / f".{version}.tmp"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        tar.extractall(tmp_dir)

    version_file = tmp_dir / "VERSION"
    backend_main = tmp_dir / "backend" / "main.py"
    if not version_file.exists() or version_file.read_text().strip() != version:
        raise RuntimeError(f"extracted release VERSION does not match expected {version}")
    if not backend_main.exists():
        raise RuntimeError("extracted release is missing backend/main.py")
    # Cheap syntax smoke check before this is ever symlinked live — catches
    # a corrupt/truncated download or a bad build before it can take the
    # backend down, without needing to actually run the new code yet.
    subprocess.run([sys.executable, "-m", "py_compile", str(backend_main)], check=True)

    shutil.rmtree(target_dir, ignore_errors=True)
    tmp_dir.rename(target_dir)
    return target_dir


def swap_current(target_dir: Path) -> None:
    """Same build-a-new-link-then-rename-over-it pattern as
    local-app-seed.py's own symlink swap — readers never see a
    half-updated `current`."""
    tmp_link = DATA_LOCAL_APP / ".current.tmp"
    tmp_link.unlink(missing_ok=True)
    tmp_link.symlink_to(target_dir, target_is_directory=True)
    tmp_link.replace(CURRENT_LINK)


def restart_services() -> None:
    # Both unit names match slide-announcer-[a-z0-9-]+\.service, already
    # allowed for this user by system/polkit/50-slide-announcer-system.rules
    # — no elevated systemd action beyond what the backend itself already
    # uses for update-check/factory-reset.
    subprocess.run(["systemctl", "restart", "slide-announcer-backend.service"], check=True)
    # Kiosk display isn't built yet (SLIDE_ANNOUNCER.md, "Kiosk display") —
    # restart it too so a frontend-asset-only release doesn't need a manual
    # reboot to pick up, once it exists. check=False: the unit may not even
    # be enabled/running yet, and that's not this script's problem.
    subprocess.run(["systemctl", "restart", "slide-announcer-kiosk.service"], check=False)


def backend_is_healthy() -> bool:
    try:
        resp = httpx.get(BACKEND_STATUS_URL, timeout=5)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def wait_for_healthy(timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if backend_is_healthy():
            return True
        time.sleep(HEALTH_CHECK_INTERVAL_SECONDS)
    return False


def prune_old_releases(keep_version: str) -> None:
    if not RELEASES_DIR.exists():
        return
    releases = sorted(
        (p for p in RELEASES_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    survivors = {v for v in (keep_version, installed_version()) if v}
    for release in releases:
        if len(survivors) >= KEEP_RELEASES:
            break
        survivors.add(release.name)
    for release in releases:
        if release.name not in survivors:
            shutil.rmtree(release, ignore_errors=True)


def apply_update(version: str, download_url: str, sha256: str | None) -> bool:
    """Returns True if the device ends up healthy — either the new version
    or a clean revert to the previous one. False only if even the revert
    failed, which is left for a human to look at rather than looping."""
    previous_version = installed_version()
    archive = RELEASES_DIR / f"{version}.tar.gz"

    try:
        log(f"downloading {version} from {download_url}")
        download(download_url, archive, sha256)
        target_dir = extract_and_smoke_check(archive, version)
    except Exception as exc:
        log(f"update to {version} failed before swap, leaving current install alone: {exc}")
        _write_status({
            "checked_at": _now_iso(), "attempted_version": version,
            "result": "failed_before_swap", "error": str(exc),
        })
        return True
    finally:
        archive.unlink(missing_ok=True)

    log(f"swapping current -> releases/{version} and restarting services")
    swap_current(target_dir)
    restart_services()

    if wait_for_healthy(HEALTH_CHECK_TIMEOUT_SECONDS):
        log(f"update to {version} healthy, pruning old releases")
        prune_old_releases(version)
        _write_status({
            "checked_at": _now_iso(), "attempted_version": version,
            "result": "success", "error": None,
        })
        return True

    log(f"update to {version} failed its health check, reverting to {previous_version}")
    if previous_version is None or not (RELEASES_DIR / previous_version).exists():
        log("no previous release available to revert to — leaving the failed version in place")
        _write_status({
            "checked_at": _now_iso(), "attempted_version": version,
            "result": "unhealthy_no_revert", "error": "no previous release to roll back to",
        })
        return False

    swap_current(RELEASES_DIR / previous_version)
    restart_services()
    reverted_healthy = wait_for_healthy(HEALTH_CHECK_TIMEOUT_SECONDS)
    _write_status({
        "checked_at": _now_iso(), "attempted_version": version,
        "result": "reverted" if reverted_healthy else "revert_unhealthy",
        "error": f"{version} failed health check" + ("" if reverted_healthy else "; revert also unhealthy"),
    })
    return reverted_healthy


def main() -> int:
    info = read_heartbeat_update_info()
    if not info or not info.get("app_update_available"):
        log("no app update available")
        return 0

    version = info.get("latest_app_version")
    download_url = info.get("app_download_url")
    if not version or not download_url:
        log("app_update_available but missing latest_app_version/app_download_url — skipping")
        return 0

    if version == installed_version():
        log(f"already on {version}")
        return 0

    status = _read_status()
    if status.get("attempted_version") == version and status.get("result") != "success":
        log(f"already attempted {version} and it did not succeed ({status.get('result')}) — "
            "waiting for a newer release rather than retrying the same one every cycle")
        return 0

    if not within_idle_window():
        log(f"update to {version} available but outside the idle window "
            f"({IDLE_WINDOW_START_HOUR:02d}:00-{IDLE_WINDOW_END_HOUR:02d}:00 local) — deferring")
        return 0

    return 0 if apply_update(version, download_url, info.get("app_sha256")) else 1


if __name__ == "__main__":
    sys.exit(main())
