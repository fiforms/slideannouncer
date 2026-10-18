#!/usr/bin/env python3
"""Escape hatch: a key combination that returns the kiosk to local Settings
whatever the browser is showing.

For a product whose main screen is a remote web page (docs/PRODUCTS.md, "A
remote-portal product"), the page can't be trusted to handle a key itself — it
may be hung, or simply not ours. This daemon reads the keyboards directly
(evdev, like volume-key-monitor.py, so it sees keys even when Chromium is wedged
and doesn't take them away from it) and, on a trigger, sends the kiosk to
LANDING_URL through slide-announcer-kiosk-navigate. The local app's own Settings
PIN gate still applies when it loads.

Triggers (COMBOS below — edit there):
  Ctrl+Alt+Home   a standard keyboard
  Ctrl+Alt+S      a standard keyboard with no Home key (laptops, compact boards)
  hold Home 3 s   the remote's Home button (KEY_HOMEPAGE; Key Debug reports it as
                  BrowserHome). Held, not tapped, so ordinary presses aren't
                  hijacked from the page.

If the redirect fails (the browser doesn't answer), it restarts the kiosk unit
instead. The restart lands on the local app, whose main screen may send the
browser straight back to the site, so a "hold" flag is dropped first
(HOLD_FLAG; the backend's POST /api/local/escape-hold/consume reads and clears
it) for the launcher to stay on Settings this once.

Opt-in per product: the unit (slide-announcer-escape-hatch.service) is
installed by core but only enabled when a product lists it in its image
`enable` file. Runs unprivileged: reading /dev/input needs the `input` group and
the polkit rule already lets slideannouncer manage slide-announcer-* units.

Device discovery is capability-based and rescanned periodically, as in
volume-key-monitor.py (whose scan code this follows; the two could share a module
one day).
"""
import glob
import os
import select
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

LANDING_URL = "http://localhost/settings"
NAVIGATE_CLI = "/usr/local/sbin/slide-announcer-kiosk-navigate"
KIOSK_UNIT = "slide-announcer-kiosk.service"
HOLD_FLAG = Path("/run/slide-announcer/escape-hatch-hold")

RESCAN_INTERVAL_SECONDS = 5.0
COOLDOWN_SECONDS = 3.0  # one trigger, however many keys the same press shuffles

CTRL = ("KEY_LEFTCTRL", "KEY_RIGHTCTRL")
ALT = ("KEY_LEFTALT", "KEY_RIGHTALT")


@dataclass(frozen=True)
class Combo:
    name: str
    # Every group needs one of its keys down. A modifier is one group of two keys
    # (left or right); a plain key is a group of one.
    groups: tuple
    hold: float = 0.0  # seconds all groups must stay down; 0 = fire on the press that completes it

    @property
    def keys(self) -> frozenset:
        return frozenset(key for group in self.groups for key in group)


COMBOS = (
    Combo("Ctrl+Alt+Home", (CTRL, ALT, ("KEY_HOME",))),
    Combo("Ctrl+Alt+S", (CTRL, ALT, ("KEY_S",))),
    Combo("hold Home (remote)", (("KEY_HOMEPAGE",),), hold=3.0),
)


def log(message: str) -> None:
    print(f"escape-hatch: {message}", flush=True)


class Detector:
    """Turns key events into triggers. Pure (no evdev, no clock of its own) so it
    can be tested: callers pass the time. Keys are tracked per input device and
    merged, so Ctrl on one keyboard and S on another still count."""

    def __init__(self, combos=COMBOS, cooldown=COOLDOWN_SECONDS):
        self.combos = combos
        self.cooldown = cooldown
        self._down = {}  # source -> set of key names
        self._armed = {}  # held combo -> when it became fully held
        self._last_fire = None

    def _pressed(self) -> set:
        return set().union(*self._down.values()) if self._down else set()

    def _held(self, combo, pressed) -> bool:
        return all(any(key in pressed for key in group) for group in combo.groups)

    def key(self, source, name, value, now) -> list:
        """Feed one key event (value 1 press, 0 release; autorepeat 2 is ignored).
        Returns the combos that fire now."""
        if value == 2:
            return []
        keys = self._down.setdefault(source, set())
        if value == 1:
            keys.add(name)
        else:
            keys.discard(name)
        pressed = self._pressed()
        fired = []
        for combo in self.combos:
            held = self._held(combo, pressed)
            if combo.hold:
                if held and combo not in self._armed and name in combo.keys and value == 1:
                    self._armed[combo] = now
                elif not held:
                    self._armed.pop(combo, None)
            elif held and value == 1 and name in combo.keys:
                fired.append(combo)
        return self._cooled(fired, now)

    def release_source(self, source) -> None:
        """An input device went away: nothing it had down is still down."""
        self._down.pop(source, None)
        pressed = self._pressed()
        for combo in list(self._armed):
            if not self._held(combo, pressed):
                del self._armed[combo]

    def poll(self, now) -> list:
        """Hold combos that have now been held long enough."""
        fired = [c for c, since in self._armed.items() if now - since >= c.hold]
        for combo in fired:
            del self._armed[combo]  # once per hold; release and press again to repeat
        return self._cooled(fired, now)

    def wait(self, now):
        """Seconds until a held combo would fire, or None."""
        if not self._armed:
            return None
        return max(0.0, min(since + c.hold for c, since in self._armed.items()) - now)

    def _cooled(self, fired, now) -> list:
        if not fired:
            return []
        if self._last_fire is not None and now - self._last_fire < self.cooldown:
            return []
        self._last_fire = now
        return fired[:1]


def escape(run=subprocess.run, hold_flag: Path = HOLD_FLAG) -> str:
    """Do the escape. Returns "navigated" or "restarted"."""
    try:
        done = run([NAVIGATE_CLI, LANDING_URL], capture_output=True, text=True, timeout=20)
        if done.returncode == 0:
            return "navigated"
        log(f"couldn't redirect the browser: {(done.stderr or '').strip() or 'exit ' + str(done.returncode)}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"couldn't redirect the browser: {exc}")
    try:
        hold_flag.write_text(str(int(time.time())))
    except OSError as exc:
        log(f"couldn't leave the stay-on-Settings flag: {exc}")
    try:
        run(["systemctl", "restart", KIOSK_UNIT], timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"couldn't restart the kiosk: {exc}")
    return "restarted"


# ------------------------------------------------------------------ input devices

def _watched_keys():
    return set().union(*(combo.keys for combo in COMBOS))


def _open_key_device(path):
    import libevdev  # not imported at module level: the detector is testable without it
    try:
        f = open(path, "rb")
    except OSError:
        return None
    try:
        dev = libevdev.Device(f)
    except OSError:
        f.close()
        return None
    codes = [getattr(libevdev.EV_KEY, name, None) for name in _watched_keys()]
    if not any(code is not None and dev.has(code) for code in codes):
        f.close()
        return None
    os.set_blocking(f.fileno(), False)
    return f, dev


def scan_key_devices(known, detector):
    """known with dead nodes dropped and new key-capable /dev/input/event* nodes added."""
    for path in list(known):
        if not os.path.exists(path):
            known[path][0].close()
            del known[path]
            detector.release_source(path)
    for path in glob.glob("/dev/input/event*"):
        if path not in known:
            opened = _open_key_device(path)
            if opened is not None:
                known[path] = opened
    return known


def main() -> int:
    import libevdev
    detector = Detector()
    devices, last_scan = {}, 0.0
    log("watching for: " + ", ".join(combo.name for combo in COMBOS))
    while True:
        now = time.monotonic()
        if now - last_scan >= RESCAN_INTERVAL_SECONDS:
            devices = scan_key_devices(devices, detector)
            last_scan = now
        if not devices:
            time.sleep(RESCAN_INTERVAL_SECONDS)
            continue

        wait = detector.wait(time.monotonic())
        timeout = RESCAN_INTERVAL_SECONDS if wait is None else min(RESCAN_INTERVAL_SECONDS, wait)
        fd_map = {f.fileno(): path for path, (f, _dev) in devices.items()}
        readable, _, _ = select.select(list(fd_map), [], [], timeout)

        fired = []
        for fd_num in readable:
            path = fd_map[fd_num]
            f, dev = devices[path]
            try:
                for event in dev.events():
                    if event.matches(libevdev.EV_KEY):
                        fired += detector.key(path, event.code.name, event.value, time.monotonic())
            except OSError:
                f.close()
                del devices[path]
                detector.release_source(path)
        fired += detector.poll(time.monotonic())

        for combo in fired[:1]:
            log(f"{combo.name}: returning to Settings -> {escape()}")


if __name__ == "__main__":
    sys.exit(main())
