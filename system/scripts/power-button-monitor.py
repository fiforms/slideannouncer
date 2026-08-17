#!/usr/bin/env python3
"""Watches the remote for a KEY_POWER press and calls
slide-announcer-display-power to toggle the kiosk's sleep state.

Nothing else claims KEY_POWER on this device, so without this it falls
through to systemd-logind's default power-key handling (suspend), which
the Pi cannot reliably resume from — see
system/logind/slide-announcer-power-button.conf, installed alongside this
script, which sets HandlePowerKey=ignore so logind gets out of the way.

The actual sleep/wake mechanism (stop/start the kiosk, blank/unblank
HDMI) lives in display-power.py, not here — this script is just one
trigger for it (see that script's own docstring for the others: a future
schedule, a future web UI menu button). Runs as its own unit
(slide-announcer-power-button.service), deliberately independent of
slide-announcer-kiosk.service — it has to keep running *while* the kiosk
is stopped/asleep, so a second press can bring it back.

Device discovery is capability-based (any /dev/input/event* node that
supports KEY_POWER) rather than pinned to a specific USB vendor/product
ID, since this device only ever has the one remote plugged in. Re-scanned
periodically so an unplug/replug is picked back up without restarting this
service.
"""
import glob
import os
import select
import subprocess
import time

import libevdev

DISPLAY_POWER_CLI = "/usr/local/sbin/slide-announcer-display-power"
RESCAN_INTERVAL_SECONDS = 5.0
DEBOUNCE_SECONDS = 1.0


def _open_power_device(path):
    try:
        f = open(path, "rb")
    except OSError:
        return None
    try:
        dev = libevdev.Device(f)
    except OSError:
        f.close()
        return None
    if not dev.has(libevdev.EV_KEY.KEY_POWER):
        f.close()
        return None
    # events() stops at "no more events buffered right now" rather than
    # blocking for the next one — that only holds if the fd is
    # non-blocking, so this can't stall the select() loop below on a
    # device that turns out to be quieter than expected.
    os.set_blocking(f.fileno(), False)
    return f, dev


def scan_power_devices(known):
    """Returns known with dead nodes dropped and any new KEY_POWER-capable
    /dev/input/event* nodes added."""
    for path in list(known):
        if not os.path.exists(path):
            known[path][0].close()
            del known[path]
    for path in glob.glob("/dev/input/event*"):
        if path in known:
            continue
        opened = _open_power_device(path)
        if opened is not None:
            known[path] = opened
    return known


def main():
    devices = {}
    last_scan = 0.0
    last_toggle = 0.0

    while True:
        now = time.monotonic()
        if now - last_scan >= RESCAN_INTERVAL_SECONDS:
            devices = scan_power_devices(devices)
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
                    if event.matches(libevdev.EV_KEY.KEY_POWER) and event.value == 1:
                        now = time.monotonic()
                        if now - last_toggle >= DEBOUNCE_SECONDS:
                            subprocess.run([DISPLAY_POWER_CLI, "toggle"], check=False)
                            last_toggle = now
            except OSError:
                f.close()
                del devices[path]


if __name__ == "__main__":
    main()
