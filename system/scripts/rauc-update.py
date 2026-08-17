#!/usr/bin/env python3
"""Slide Announcer RAUC update CLI (Tier 1 OS OTA) — manual/testing use.

Automatic installation now exists as a separate script/unit,
os-updater.py (slide-announcer-os-updater.service/.timer) — it wraps the
same `rauc install`/tryboot calls this CLI does, on a timer, gated by
os_auto_update_enabled and the idle window (see SLIDE_ANNOUNCER.md, Tier 1,
"Update safety"). This CLI remains useful for manual testing/intervention
(e.g. a device with auto-update disabled, or forcing an install of a
specific bundle) independent of that automation.

system/rauc/rpi-tryboot-backend.sh and rpi-tryboot-commit.sh's install →
tryboot reboot → commit cycle is confirmed working end-to-end on real
hardware (2026-08-11) — see those files' own headers. Still unverified:
the forced-bad-health → rollback path, since today's post-tryboot health
check is just a placeholder ("we reached this unit").

Subcommands:
    check                   call the server heartbeat, print the OS update
                             fields
    install [URL_OR_PATH]   `rauc install` the given bundle, or (with no
                             argument) whatever `check` reports as available
    tryboot                 reboot into the slot `install` just staged, via
                             Raspberry Pi's tryboot (one-shot; reverts to
                             the current slot on the next boot unless
                             rpi-tryboot-commit.sh commits it first)
    status                  `rauc status`
    mark-good               `rauc status mark-good`

`install`/`status`/`mark-good` go through RAUC's own D-Bus service (which
runs as root regardless of caller) and need no privilege here. `tryboot`
needs none either — it starts slide-announcer-tryboot.service (a root
oneshot unit that runs the actual `reboot "0 tryboot"`, since that writes
straight to /run/systemd/reboot-param, bypassing D-Bus entirely) via
`systemctl start`, authorized for this account by
system/polkit/50-slide-announcer-system.rules rather than sudo.
"""
import argparse
import json
import platform
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

BOOT_YAML = Path("/boot/firmware/slideannouncer.yaml")
DEVICE_TOKEN_FILE = Path("/data/device-token")
VERSION_FILE = Path("/opt/slide-announcer/VERSION")


def read_server_url():
    """See local-app/backend/pairing.py's read_server_url() — same
    server_url field in /boot/firmware/slideannouncer.yaml, same
    fail-closed behavior, duplicated here since this CLI runs outside the
    local-app backend's venv/process.
    """
    if not BOOT_YAML.exists():
        sys.exit(f"{BOOT_YAML} missing — this device has no boot config at all.")
    data = yaml.safe_load(BOOT_YAML.read_text()) or {}
    server_url = data.get("server_url")
    if not server_url:
        sys.exit(f"server_url is not set in {BOOT_YAML}. See provisioning/slideannouncer.yaml.example.")
    return server_url.rstrip("/")


def read_device_token():
    if not DEVICE_TOKEN_FILE.exists():
        return None
    return DEVICE_TOKEN_FILE.read_text().strip()


def read_os_version():
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return None


def heartbeat():
    server_url = read_server_url()
    token = read_device_token()
    if not token:
        sys.exit(
            f"No device token at {DEVICE_TOKEN_FILE} — this device isn't "
            "paired yet (see local-app's Pairing screen, or "
            "SLIDE_ANNOUNCER.md's Pairing flow). To test the RAUC pipeline "
            "without pairing, use 'install <url-or-path>' directly instead "
            "of 'check'/plain 'install'."
        )

    body = json.dumps({
        "os_version": read_os_version(),
        "architecture": platform.machine(),
    }).encode()
    req = urllib.request.Request(
        f"{server_url}/api/slide-announcers/heartbeat",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            sys.exit(
                "Server rejected this device's token (401) — revoked or "
                "unpaired. Not handling the wipe-and-reboot flow here "
                "(see SLIDE_ANNOUNCER.md's Heartbeat/revocation section) — "
                "this CLI is test tooling, not the real update-check unit."
            )
        sys.exit(f"Heartbeat failed: HTTP {e.code} {e.reason}")
    except urllib.error.URLError as e:
        sys.exit(f"Heartbeat failed: {e.reason} (is {server_url} correct and reachable?)")


def cmd_check(args):
    data = heartbeat()
    print(json.dumps(data, indent=2))
    if data.get("os_update_available"):
        print(f"\nOS update available: {data.get('latest_os_version')} — {data.get('os_bundle_url')}")
        if not data.get("os_auto_update_enabled", True):
            print("os_auto_update_enabled is false — server says report only, don't auto-install.")
    else:
        print("\nNo OS update available.")
    return data


def run_rauc(*args):
    try:
        print(f"+ rauc {' '.join(args)}")
        return subprocess.run(["rauc", *args])
    except FileNotFoundError:
        sys.exit("'rauc' not found — run this on the device, not the build host.")


def cmd_install(args):
    bundle = args.bundle
    if not bundle:
        data = cmd_check(args)
        bundle = data.get("os_bundle_url")
        if not bundle:
            sys.exit(
                "No bundle URL to install (server reports no update "
                "available). Pass one explicitly to force a test install: "
                "'install <url-or-path-to.raucb>'."
            )

    print(f"\nInstalling: {bundle}")
    result = run_rauc("install", bundle)
    if result.returncode == 0:
        print(
            "\nInstall succeeded — rpi-tryboot-backend.sh has staged the "
            "new slot (/boot/firmware/tryboot.txt). Nothing has rebooted "
            "yet. Run 'slide-announcer-update tryboot' to actually "
            "boot into it, or 'status' to inspect RAUC's view first."
        )
    sys.exit(result.returncode)


def cmd_tryboot(args):
    if not args.yes:
        sys.exit(
            "This reboots the device right now, into whatever slot the "
            "last 'install' staged (see /boot/firmware/tryboot.txt). "
            "Pass --yes to actually do it."
        )
    print(
        "Rebooting via tryboot. Check 'slide-announcer-update status' and "
        "'journalctl -u slide-announcer-tryboot-check' once it's back up "
        "to confirm the new slot committed."
    )
    # --no-block: slide-announcer-tryboot.service's own ExecStart reboots
    # the machine, so a plain (blocking) `systemctl start` sometimes has its
    # job canceled by the shutdown before it can report success back here
    # — a false-negative "failed to start" printed on the way out, even
    # though the reboot itself already fired. --no-block just enqueues the
    # job and returns immediately, before that race can happen.
    result = subprocess.run(["systemctl", "start", "--no-block", "slide-announcer-tryboot.service"])
    if result.returncode != 0:
        sys.exit(
            "Failed to start slide-announcer-tryboot.service — check "
            "system/polkit/50-slide-announcer-system.rules is installed "
            "and covers this account."
        )
    sys.exit(0)


def cmd_status(args):
    sys.exit(run_rauc("status").returncode)


def cmd_mark_good(args):
    print(
        "Note: this is meant to run automatically after a post-update "
        "health check passes — no such health check exists yet. Calling it "
        "directly here only tests the RAUC command itself.\n"
    )
    sys.exit(run_rauc("status", "mark-good").returncode)


def main():
    parser = argparse.ArgumentParser(
        description="Slide Announcer RAUC OS update CLI (manual/testing use — see this file's module docstring)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="Call the server heartbeat, print OS update fields").set_defaults(func=cmd_check)

    p_install = sub.add_parser("install", help="Install a RAUC bundle (from the server check, or an explicit URL/path)")
    p_install.add_argument("bundle", nargs="?", help="Bundle URL or local path; omit to use whatever 'check' reports")
    p_install.set_defaults(func=cmd_install)

    p_tryboot = sub.add_parser("tryboot", help="Reboot into the slot the last 'install' staged (disruptive)")
    p_tryboot.add_argument("--yes", action="store_true", help="Actually reboot (required)")
    p_tryboot.set_defaults(func=cmd_tryboot)

    sub.add_parser("status", help="rauc status").set_defaults(func=cmd_status)
    sub.add_parser("mark-good", help="rauc status mark-good").set_defaults(func=cmd_mark_good)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
