#!/usr/bin/env python3
"""Watches the remote for KEY_VOLUMEUP/KEY_VOLUMEDOWN/KEY_MUTE and adjusts
PipeWire's default sink via wpctl, persisting the result to
/data/status/audio-volume and /data/status/audio-muted.

Division of labor mirrors power-button-monitor.py/display-power.py: this
script is the input trigger and the thing that actually calls wpctl; the
two /data files are the shared state everything else reads — re-applied
at boot/output-switch time by apply-audio-output.sh, and read (not
written) by the backend's pairing.py for the /api/local/audio-volume
endpoint the kiosk page polls to reflect changes on screen.

Runs as its own unit (slide-announcer-volume-key.service), independent of
the kiosk, same reasoning as slide-announcer-power-button.service: the
remote should still change the volume even while the kiosk is asleep,
stopped, or restarting.

Device discovery mirrors power-button-monitor.py: capability-based (any
/dev/input/event* node exposing at least one of the three keys) rather
than pinned to a vendor/product ID, re-scanned periodically so an
unplug/replug is picked back up without restarting this service.
"""
import glob
import os
import select
import subprocess
import time
from pathlib import Path

import libevdev

VOLUME_FILE = Path("/data/status/audio-volume")
MUTED_FILE = Path("/data/status/audio-muted")
DEFAULT_VOLUME = 100
VOLUME_MIN = 0
VOLUME_MAX = 100
STEP_PERCENT = 5

WATCHED_KEYS = (
    libevdev.EV_KEY.KEY_VOLUMEUP,
    libevdev.EV_KEY.KEY_VOLUMEDOWN,
    libevdev.EV_KEY.KEY_MUTE,
)

RESCAN_INTERVAL_SECONDS = 5.0
# Shorter than power-button-monitor.py's 1s debounce — a volume rocker is
# expected to register several presses (or an autorepeat run) per second,
# unlike a power toggle where a doubled event would be actively wrong.
DEBOUNCE_SECONDS = 0.15


def read_volume():
    if not VOLUME_FILE.exists():
        return DEFAULT_VOLUME
    try:
        value = int(VOLUME_FILE.read_text().strip())
    except ValueError:
        return DEFAULT_VOLUME
    return max(VOLUME_MIN, min(VOLUME_MAX, value))


def write_volume(value):
    VOLUME_FILE.parent.mkdir(parents=True, exist_ok=True)
    VOLUME_FILE.write_text(str(value))
    VOLUME_FILE.chmod(0o644)


def read_muted():
    if not MUTED_FILE.exists():
        return False
    return MUTED_FILE.read_text().strip() == "true"


def write_muted(value):
    MUTED_FILE.parent.mkdir(parents=True, exist_ok=True)
    MUTED_FILE.write_text("true" if value else "false")
    MUTED_FILE.chmod(0o644)


def apply_volume(value):
    subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{value}%"], check=False)


def apply_muted(value):
    subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if value else "0"], check=False)


def adjust_volume(delta):
    # A volume press while muted unmutes first — matches how a normal TV
    # remote behaves (the press is trying to make sound audible, not
    # silently ratchet a level nobody can hear yet).
    if read_muted():
        write_muted(False)
        apply_muted(False)
    value = max(VOLUME_MIN, min(VOLUME_MAX, read_volume() + delta))
    write_volume(value)
    apply_volume(value)


def toggle_mute():
    value = not read_muted()
    write_muted(value)
    apply_muted(value)


def _open_key_device(path):
    try:
        f = open(path, "rb")
    except OSError:
        return None
    try:
        dev = libevdev.Device(f)
    except OSError:
        f.close()
        return None
    if not any(dev.has(key) for key in WATCHED_KEYS):
        f.close()
        return None
    # Same non-blocking rationale as power-button-monitor.py: events()
    # stops at "nothing buffered right now" rather than blocking, which
    # only holds with a non-blocking fd.
    os.set_blocking(f.fileno(), False)
    return f, dev


def scan_key_devices(known):
    """Returns known with dead nodes dropped and any new key-capable
    /dev/input/event* nodes added."""
    for path in list(known):
        if not os.path.exists(path):
            known[path][0].close()
            del known[path]
    for path in glob.glob("/dev/input/event*"):
        if path in known:
            continue
        opened = _open_key_device(path)
        if opened is not None:
            known[path] = opened
    return known


def main():
    # PipeWire/WirePlumber run under the same manually-built
    # XDG_RUNTIME_DIR that kiosk-start.sh exports before backgrounding them
    # (see that script's own comment) — there's no systemd --user session
    # for this unit to inherit it from automatically (unlike
    # slide-announcer-kiosk.service, which gets one via PAMName=login).
    # Without this, wpctl silently talks to no PipeWire instance at all
    # rather than erroring: the file-based state and on-screen indicator
    # still update, but nothing audible actually changes.
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")

    devices = {}
    last_scan = 0.0
    last_action = 0.0

    while True:
        now = time.monotonic()
        if now - last_scan >= RESCAN_INTERVAL_SECONDS:
            devices = scan_key_devices(devices)
            last_scan = now

        if not devices:
            time.sleep(RESCAN_INTERVAL_SECONDS)
            continue

        fd_map = {f.fileno(): path for path, (f, _dev) in devices.items()}
        readable, _, _ = select.select(list(fd_map), [], [], RESCAN_INTERVAL_SECONDS)

        for fd_num in readable:
            path = fd_map[fd_num]
            f, dev = devices[path]
            try:
                for event in dev.events():
                    # value 1 = press, 2 = autorepeat (key held down) — both
                    # keep adjusting volume while a rocker is held. Mute
                    # only reacts to the initial press: a held mute key
                    # isn't meant to keep re-toggling.
                    if event.value not in (1, 2):
                        continue
                    now = time.monotonic()
                    if now - last_action < DEBOUNCE_SECONDS:
                        continue
                    if event.matches(libevdev.EV_KEY.KEY_VOLUMEUP):
                        adjust_volume(STEP_PERCENT)
                        last_action = now
                    elif event.matches(libevdev.EV_KEY.KEY_VOLUMEDOWN):
                        adjust_volume(-STEP_PERCENT)
                        last_action = now
                    elif event.matches(libevdev.EV_KEY.KEY_MUTE) and event.value == 1:
                        toggle_mute()
                        last_action = now
            except OSError:
                f.close()
                del devices[path]


if __name__ == "__main__":
    main()
