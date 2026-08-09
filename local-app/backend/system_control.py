"""Privileged system operations (reboot, OTA update check) triggered from
the web UI, without the backend itself ever running as root.

The backend (`slideannouncer` user) is granted exactly two things via
polkit (see system/polkit/50-slide-announcer-system.rules): logind's own
reboot action, and permission to `systemctl start` units whose name
matches `slide-announcer-*.service` — never arbitrary system units, never
root shell access. The actual privileged work (calling `rauc`/whatever a
future update-install needs) runs as root *inside* those units, not in
this process.
"""
import asyncio
import json
from pathlib import Path

UPDATE_CHECK_STATUS_FILE = Path("/data/status/update-check.json")


class SystemCommandError(RuntimeError):
    pass


async def reboot() -> None:
    proc = await asyncio.create_subprocess_exec("systemctl", "reboot")
    await proc.wait()
    if proc.returncode != 0:
        raise SystemCommandError(
            "systemctl reboot failed — check system/polkit/50-slide-announcer-system.rules is installed"
        )


async def trigger_update_check() -> dict | None:
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "start", "slide-announcer-update-check.service",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise SystemCommandError(
            stderr.decode(errors="replace").strip() or "failed to start slide-announcer-update-check.service"
        )
    # `systemctl start` on a oneshot unit blocks until it finishes (no
    # --no-block passed), so the status file is already written by now.
    return read_update_check_status()


def read_update_check_status() -> dict | None:
    if not UPDATE_CHECK_STATUS_FILE.exists():
        return None
    return json.loads(UPDATE_CHECK_STATUS_FILE.read_text())


async def trigger_factory_reset() -> None:
    """Starts slide-announcer-factory-reset-trigger.service, which sets
    /boot/firmware/FACTORY_RESET and reboots — see
    system/scripts/factory-reset-check.sh for what happens from there
    (reformats /data before it's ever mounted, then boots exactly like a
    fresh SD card: WiFi, pairing, cached slides, and local-app all get
    reset/reseeded).
    """
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "start", "slide-announcer-factory-reset-trigger.service",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise SystemCommandError(
            stderr.decode(errors="replace").strip() or "failed to start slide-announcer-factory-reset-trigger.service"
        )
