"""Periodic heartbeat client — POSTs device metrics to the server every 5
minutes (see SLIDE_ANNOUNCER.md, "Heartbeat + version checks") and folds
the response's update-availability fields into a local status file. Runs
as a background asyncio task inside this backend (started from main.py's
lifespan), not a separate systemd timer — heartbeat delivery doesn't need
root and doesn't need to survive a backend crash independently of the rest
of this process.

A network-level failure (timeout, DNS, connection refused) is recorded in
the status file and otherwise ignored — nothing else to do yet, since the
slide sync daemon (the thing whose cache this would protect) is still a
stub. A 401 is different in kind: it means the *server* was reached and
explicitly rejected this device's token (revoked/unpaired from the
website), which triggers the same wipe-and-reboot path an explicit local
unpair uses — see pairing.py and SLIDE_ANNOUNCER.md's Heartbeat/revocation
section.
"""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

import pairing
import system_control

INTERVAL_SECONDS = 5 * 60

OS_VERSION_FILE = Path("/opt/slide-announcer/VERSION")
APP_VERSION_FILE = Path("/data/local-app/current/VERSION")
CPU_TEMP_FILE = Path("/sys/class/thermal/thermal_zone0/temp")
STATUS_FILE = Path("/data/status/heartbeat.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_os_version() -> str | None:
    if OS_VERSION_FILE.exists():
        return OS_VERSION_FILE.read_text().strip()
    return None


def read_app_version() -> str | None:
    if APP_VERSION_FILE.exists():
        return APP_VERSION_FILE.read_text().strip()
    return None


def read_cpu_temp_c() -> float | None:
    """Raspberry Pi's SoC thermal zone — reads in millidegrees C."""
    if not CPU_TEMP_FILE.exists():
        return None
    try:
        return int(CPU_TEMP_FILE.read_text().strip()) / 1000
    except ValueError:
        return None


def _write_status(data: dict) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(data))
    # Explicit chmod, not just this process's umask — the same lesson
    # local-app-seed.py and update-check.py's status files already apply:
    # readers of this file (the About screen, via GET /api/local/status)
    # aren't necessarily the same user/process that wrote it.
    STATUS_FILE.chmod(0o644)


def read_status() -> dict | None:
    if not STATUS_FILE.exists():
        return None
    try:
        return json.loads(STATUS_FILE.read_text())
    except json.JSONDecodeError:
        return None


async def send_once() -> None:
    """Sends one heartbeat. A no-op if unpaired — nothing to report yet."""
    token = pairing.read_device_token()
    if not token:
        return

    server_url = pairing.read_server_url()
    payload = {
        "app_version": read_app_version(),
        "os_version": read_os_version(),
        "cpu_temp_c": read_cpu_temp_c(),
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{server_url}/api/slide-announcers/heartbeat",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.RequestError as exc:
        _write_status({"last_attempt_at": _now_iso(), "last_error": str(exc)})
        return

    if resp.status_code == 401:
        pairing.unpair_and_wipe()
        _write_status({"last_attempt_at": _now_iso(), "last_error": "revoked"})
        await system_control.reboot()
        return

    if resp.status_code >= 400:
        _write_status({"last_attempt_at": _now_iso(), "last_error": f"HTTP {resp.status_code}"})
        return

    _write_status({
        "last_attempt_at": _now_iso(),
        "last_success_at": _now_iso(),
        "last_error": None,
        "response": resp.json(),
    })


async def run_forever() -> None:
    while True:
        try:
            await send_once()
        except Exception as exc:  # a bug here must never take the backend down
            _write_status({"last_attempt_at": _now_iso(), "last_error": f"unexpected: {exc}"})
        await asyncio.sleep(INTERVAL_SECONDS)
