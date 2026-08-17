#!/usr/bin/env python3
"""Applies /boot/firmware/slideannouncer.yaml's `vt_lockswitch: locked` flag,
if present, by setting the kernel's VT_LOCKSWITCH flag.

This is what actually blocks Ctrl+Alt+F# console switching — see
system/labwc/rc.xml's own comment: that's a compositor-level keybind
mechanism and never sees a VT-switch request at all, since the kernel's
console keyboard driver decodes and acts on Alt+Fn before labwc/Wayland are
ever in the loop. VT_LOCKSWITCH is the kernel's own lock for exactly this
(the same primitive Xorg's `-novtswitch` uses internally); it needs
CAP_SYS_TTY_CONFIG, hence this script runs as root via a oneshot unit
rather than from kiosk-start.sh (which runs as the unprivileged
`slideannouncer` user).

Run once per boot by slide-announcer-vtlock.service, before the kiosk
starts. Best-effort and idempotent: a missing/malformed boot-yaml or an
absent/mismatched flag is not an error, just "leave VT switching alone."
"""
import fcntl
import os
from pathlib import Path

import yaml

BOOT_YAML = Path("/boot/firmware/slideannouncer.yaml")
VT_LOCKSWITCH = 0x560B


def main() -> None:
    if not BOOT_YAML.exists():
        return
    data = yaml.safe_load(BOOT_YAML.read_text()) or {}
    if data.get("vt_lockswitch") != "locked":
        return

    # O_NOCTTY: this process has no business acquiring tty0 as its own
    # controlling terminal just to issue one ioctl against it.
    fd = os.open("/dev/tty0", os.O_RDWR | os.O_NOCTTY)
    try:
        fcntl.ioctl(fd, VT_LOCKSWITCH, 0)
    finally:
        os.close(fd)
    print("slide-announcer-vtlock: VT switching locked (vt_lockswitch: locked in slideannouncer.yaml)")


if __name__ == "__main__":
    main()
