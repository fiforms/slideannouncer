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

DISPLAY_POWER_CLI = "/usr/local/sbin/slide-announcer-display-power"
AUDIO_OUTPUT_CLI = "/usr/local/sbin/slide-announcer-apply-audio-output"

UPDATE_CHECK_STATUS_FILE = Path("/data/status/update-check.json")
# Written by whichever of os-updater.py / local_app_updater.py is currently
# running (see either script's PROGRESS_FILE comment) — one shared file so
# the Settings UI only has to poll one place.
UPDATE_PROGRESS_FILE = Path("/data/status/update-progress.json")

# Every unit that can ever run an install for either tier — both the
# timer-driven ones and the on-demand "-now" ones this apply flow starts.
# Checked before starting a new apply so two updates (a manual click
# racing the nightly timer, or two clicks racing each other) can't run at
# once — belt-and-suspenders alongside the flock the scripts themselves
# take (see os-updater.py's acquire_lock()), which is the real guarantee;
# this is just what lets the UI reject a second click immediately with a
# clear message instead of silently queuing or erroring deep in systemd.
GUARDED_UPDATE_UNITS = [
    "slide-announcer-os-updater.service",
    "slide-announcer-os-updater-now.service",
    "slide-announcer-local-app-updater.service",
    "slide-announcer-local-app-updater-now.service",
]


class SystemCommandError(RuntimeError):
    pass


class UpdateAlreadyRunningError(SystemCommandError):
    pass


async def reboot() -> None:
    proc = await asyncio.create_subprocess_exec("systemctl", "reboot")
    await proc.wait()
    if proc.returncode != 0:
        raise SystemCommandError(
            "systemctl reboot failed — check system/polkit/50-slide-announcer-system.rules is installed"
        )


async def sleep_display() -> None:
    """Stops slide-announcer-kiosk.service and blanks the HDMI output —
    see system/scripts/display-power.py. No polkit rule needed for this
    one: unlike reboot() above, `slide-announcer-display-power` itself is
    already callable by this process's own unprivileged `slideannouncer`
    user (systemctl on our own unit is polkit-granted; vcgencmd only needs
    the `video` group, which this account already has) — this just shells
    out to it directly.

    There's no matching wake_display() here on purpose: once the kiosk is
    stopped the browser rendering this very Settings page is gone, so a
    "Wake" button in this UI could never actually be reached. Waking back
    up is the remote's power button, or `slide-announcer-display-power
    wake` run directly (SSH as slideadmin, or once a schedule/timer exists
    for it).
    """
    proc = await asyncio.create_subprocess_exec(DISPLAY_POWER_CLI, "sleep")
    await proc.wait()
    if proc.returncode != 0:
        raise SystemCommandError(f"{DISPLAY_POWER_CLI} sleep failed (exit {proc.returncode})")


async def apply_audio_output() -> None:
    """Re-runs PipeWire's default-sink selection against whatever
    pairing.read_audio_output() currently says. No polkit rule needed —
    same reasoning as sleep_display() above: PipeWire/WirePlumber run as
    this same unprivileged `slideannouncer` user (started directly by
    kiosk-start.sh, not a separate root-owned unit), so `wpctl` is already
    reachable without elevation. Failures are logged by the script itself
    rather than raised — a missing/renamed ALSA sink shouldn't 500 the
    Settings page, it should just leave the previous output in place.
    """
    proc = await asyncio.create_subprocess_exec(AUDIO_OUTPUT_CLI)
    await proc.wait()


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


def _read_status_file(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


async def _unit_is_running(unit: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "is-active", unit,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    # A oneshot unit (no RemainAfterExit) reports "activating" while its
    # ExecStart is still running and "inactive" as soon as it exits — that
    # window *is* "currently installing," which is exactly what this checks.
    # "reloading"/"active" are included for completeness; not states these
    # particular units actually pass through, but harmless to also treat as
    # "running" if that ever changes.
    return stdout.decode().strip() in ("active", "activating", "reloading")


async def currently_running_update() -> str | None:
    for unit in GUARDED_UPDATE_UNITS:
        if await _unit_is_running(unit):
            return unit
    return None


def read_update_progress() -> dict | None:
    return _read_status_file(UPDATE_PROGRESS_FILE)


async def trigger_update_apply() -> dict:
    """Starts whichever tier the last "Check for Update" found available,
    per SLIDE_ANNOUNCER.md's existing update ordering: an OS update (hotfix
    or full — os-updater.py itself tells those two apart via
    os_release_type) always goes first, then the local-app update, never
    both from one click — see slide-announcer-os-updater-now.service /
    slide-announcer-local-app-updater-now.service's own headers for why
    that's safe to do without the timer-driven units' idle-window/cross-tier
    gating.

    Starts the unit with --no-block and returns immediately rather than
    waiting for it to finish — a full OS image install can take a long
    time on a slow network, far longer than a request should block for.
    Progress/completion is polled separately via read_update_progress().
    """
    running = await currently_running_update()
    if running:
        raise UpdateAlreadyRunningError(f"An update is already running ({running}) — wait for it to finish.")

    data = (read_update_check_status() or {}).get("data") or {}

    if data.get("os_update_available"):
        service, kind = "slide-announcer-os-updater-now.service", "os"
    elif data.get("app_update_available"):
        service, kind = "slide-announcer-local-app-updater-now.service", "app"
    else:
        raise SystemCommandError("No update available to apply — run a check first.")

    proc = await asyncio.create_subprocess_exec(
        "systemctl", "start", "--no-block", service,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise SystemCommandError(stderr.decode(errors="replace").strip() or f"failed to start {service}")

    return {"started": True, "kind": kind}


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
