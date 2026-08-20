#!/usr/bin/env python3
"""Toggle or query the kiosk's sleep state: stop/start
slide-announcer-kiosk.service and blank/unblank the HDMI output.

This is the one place the actual sleep/wake mechanism lives — every
trigger (the remote's power key via power-button-monitor.py; the SRT
video-sink daemon, system/scripts/srt-sink-monitor.py, via the `takeover`
action below; in future, a systemd timer for a schedule, or a menu button
in the local web UI backed by a oneshot slide-announcer-*.service the
backend starts) just calls this CLI rather than each re-implementing the
toggle.

Callable directly, without sudo, as root, `slideannouncer`, or
`slideadmin` — no setuid helper, no new polkit rule needed:
- `systemctl stop/start slide-announcer-kiosk.service` is already
  polkit-granted to all three (system/polkit/50-slide-announcer-system.rules
  matches any `slide-announcer-*.service` unit for both non-root users).
- `vcgencmd display_power` only needs `video` group membership, which both
  accounts already have persistently (`slideannouncer`'s `useradd
  --groups video,...` and `slideadmin`'s stock pi-gen group set — see
  stage-slide-announcer/01-system-files/00-run.sh).
- The sleep-state marker directory (/run/slide-announcer) is created by
  slide-announcer-power-button-dirs.service, owned root:slideannouncer
  mode 0775 — both accounts are members of the `slideannouncer` group
  (slideadmin explicitly so, via the same 00-run.sh), so both can create/
  remove the marker file in it.

Usage: slide-announcer-display-power {sleep|wake|toggle|status}
"""
import argparse
import os
import subprocess

KIOSK_UNIT = "slide-announcer-kiosk.service"
SLEEP_MARKER = "/run/slide-announcer/kiosk-sleeping"


def is_sleeping():
    return os.path.exists(SLEEP_MARKER)


def sleep():
    subprocess.run(["systemctl", "stop", KIOSK_UNIT], check=False)
    subprocess.run(["vcgencmd", "display_power", "0"], check=False)
    open(SLEEP_MARKER, "w").close()
    print("kiosk stopped, HDMI blanked")


def wake():
    subprocess.run(["vcgencmd", "display_power", "1"], check=False)
    subprocess.run(["systemctl", "start", KIOSK_UNIT], check=False)
    try:
        os.remove(SLEEP_MARKER)
    except FileNotFoundError:
        pass
    print("HDMI on, kiosk starting")


def toggle():
    wake() if is_sleeping() else sleep()


def takeover():
    """Prepares the display for external video (SRT sink) without
    touching the sleep-state marker: stops the kiosk if it's running, and
    makes sure HDMI is actually on, but doesn't flip is_sleeping() either
    way. Pairs with a later unconditional wake()/sleep() call — based on a
    was_sleeping flag the caller captures via `status` *before* calling
    this — to restore the correct end state afterward. That pairing is
    only correct because both wake() and sleep() are already idempotent:
    whichever one runs, stopping an already-stopped kiosk unit and
    blanking an already-blank (or unblanking an already-on) display are
    both no-ops, so the end state comes out right regardless of which
    state we started in. See srt-sink-monitor.py, the only caller today.
    """
    subprocess.run(["systemctl", "stop", KIOSK_UNIT], check=False)
    subprocess.run(["vcgencmd", "display_power", "1"], check=False)
    print("kiosk stopped, HDMI on (sleep-state unchanged) — ready for external video")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["sleep", "wake", "toggle", "status", "takeover"])
    args = parser.parse_args()

    if args.action == "status":
        print("sleeping" if is_sleeping() else "awake")
    else:
        {"sleep": sleep, "wake": wake, "toggle": toggle, "takeover": takeover}[args.action]()


if __name__ == "__main__":
    main()
