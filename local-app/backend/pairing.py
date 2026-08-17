"""Device-side pairing client against the server's pairing API
(App\\Http\\Controllers\\Api\\SlideAnnouncerPairingController) — see
SLIDE_ANNOUNCER.md, "Pairing flow." Also owns the single wipe-and-reboot
code path shared by an explicit local "Unpair" action and a revoked-token
401 detected by heartbeat.py — see SLIDE_ANNOUNCER.md's Heartbeat/
revocation section and "Kiosk display."

The device token itself (proof of pairing) lives at /data/device-token,
chmod 640 (owner slideannouncer, group-readable) — same ext4 persistence
rationale as identity.key: it must survive an OS update and never end up
on the FAT32 boot partition, which is world-readable by design. Group-
readable rather than owner-only so the interactive `slideadmin` account
(in the `slideannouncer` group — see image-builder's 00-run.sh) can run
`slide-announcer-update check`/`install` over SSH without sudo; see that
CLI's own docstring.
"""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import httpx

import identity

SERVER_URL_FILE = Path("/etc/slide-announcer/server-url")
DEVICE_TOKEN_FILE = Path("/data/device-token")
# Written by provisioning/firstboot.py on every boot from the boot
# partition's slideannouncer.yaml `language` key — see that script's
# write_language_boot_hint(). Purely a pre-pairing default; never touched
# by this module, only read as a fallback by read_effective_language()
# below.
LANGUAGE_BOOT_HINT_FILE = Path("/data/status/language-boot-hint.json")
# The server is authoritative for this device's display name once paired
# (an entity admin can rename it from the fleet UI) — this file is just a
# local cache so the Pairing screen has something to show immediately after
# pairing and between heartbeats, written first by pair() with whatever the
# user typed, then kept in sync by heartbeat.py from each heartbeat
# response's device_name field.
DEVICE_NAME_FILE = Path("/data/status/device-name")
# Same local-cache rationale as DEVICE_NAME_FILE, for the entity (church/
# school) this device is currently paired to — set from the pairing
# response's entity_name, then kept in sync by heartbeat.py, since a
# re-pair can move a device to a different entity without device_name
# changing at all.
ENTITY_NAME_FILE = Path("/data/status/entity-name")
# Server-assigned language, once paired — kept in sync by heartbeat.py from
# each heartbeat response's `language` field, same pattern as
# DEVICE_NAME_FILE/ENTITY_NAME_FILE. Always wins over LANGUAGE_BOOT_HINT_FILE
# once it exists; see read_effective_language().
LANGUAGE_FILE = Path("/data/status/language")

# Wiped together, always — see this module's docstring for the three
# triggers that share this list (explicit unpair, 401 revocation, and
# provisioning.py's own identity-mismatch wipe, which duplicates this list
# rather than importing it since that script runs standalone, as root,
# before this backend exists).
WIPE_PATHS = [
    DEVICE_TOKEN_FILE,
    DEVICE_NAME_FILE,
    ENTITY_NAME_FILE,
    LANGUAGE_FILE,
    Path("/data/slides"),
    Path("/data/local-app/settings.json"),
]


class PairingError(RuntimeError):
    """Raised with a message safe to show directly on the pairing screen."""


def read_server_url() -> str:
    if not SERVER_URL_FILE.exists():
        raise PairingError(
            f"{SERVER_URL_FILE} missing — this image was built without "
            "SLIDE_ANNOUNCER_SERVER_URL set."
        )
    return SERVER_URL_FILE.read_text().strip()


def is_paired() -> bool:
    return DEVICE_TOKEN_FILE.exists()


def read_device_token() -> str | None:
    if not DEVICE_TOKEN_FILE.exists():
        return None
    return DEVICE_TOKEN_FILE.read_text().strip()


def read_device_name() -> str | None:
    if not DEVICE_NAME_FILE.exists():
        return None
    return DEVICE_NAME_FILE.read_text().strip() or None


def write_device_name(name: str) -> None:
    DEVICE_NAME_FILE.parent.mkdir(parents=True, exist_ok=True)
    DEVICE_NAME_FILE.write_text(name)
    DEVICE_NAME_FILE.chmod(0o644)


def read_entity_name() -> str | None:
    if not ENTITY_NAME_FILE.exists():
        return None
    return ENTITY_NAME_FILE.read_text().strip() or None


def write_entity_name(name: str) -> None:
    ENTITY_NAME_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENTITY_NAME_FILE.write_text(name)
    ENTITY_NAME_FILE.chmod(0o644)


def read_language() -> str | None:
    """Server-assigned language code, if this device has paired and the
    server has one on file. None if unpaired, or paired but not yet
    assigned one — see read_effective_language() for the boot-yaml
    fallback that applies in both of those cases."""
    if not LANGUAGE_FILE.exists():
        return None
    return LANGUAGE_FILE.read_text().strip() or None


def write_language(code: str) -> None:
    LANGUAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LANGUAGE_FILE.write_text(code)
    LANGUAGE_FILE.chmod(0o644)


def read_language_boot_hint() -> str | None:
    if not LANGUAGE_BOOT_HINT_FILE.exists():
        return None
    try:
        data = json.loads(LANGUAGE_BOOT_HINT_FILE.read_text())
    except json.JSONDecodeError:
        return None
    return data.get("code")


def read_effective_language() -> str | None:
    """The language the device should actually use right now: the
    server-assigned value once paired (authoritative and never reverts to
    the boot-yaml hint while paired — see LOCALIZATION_TODO.md), falling
    back to provisioning/firstboot.py's boot-yaml hint before pairing or if
    the server hasn't assigned one yet.
    """
    return read_language() or read_language_boot_hint()


def read_paired_at() -> str | None:
    """ISO8601 timestamp of when this device paired, for the Settings >
    Pairing screen's "paired since" display. The token file is only ever
    (re)written by pair() below, so its mtime is exactly that moment —
    no separate paired-at record needed.
    """
    if not DEVICE_TOKEN_FILE.exists():
        return None
    mtime = DEVICE_TOKEN_FILE.stat().st_mtime
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


async def pair(code: str, device_name: str) -> dict:
    """POSTs the pairing code to the server. Raises PairingError (message
    safe to render as-is) on a bad/expired code, rate-limiting, or a
    network failure reaching the server.
    """
    server_url = read_server_url()
    payload = {
        "code": code,
        "device_name": device_name,
        "mac_address": identity.get_mac_address(),
        "device_uuid": identity.get_device_uuid(),
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{server_url}/api/slide-announcers/pair", json=payload)
    except httpx.RequestError as exc:
        raise PairingError(f"Could not reach the server: {exc}") from exc

    if resp.status_code == 422:
        raise PairingError("Invalid or expired pairing code.")
    if resp.status_code == 429:
        raise PairingError("Too many pairing attempts — wait a few minutes and try again.")
    if resp.status_code >= 400:
        raise PairingError(f"Pairing failed (server said HTTP {resp.status_code}).")

    data = resp.json()
    DEVICE_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    DEVICE_TOKEN_FILE.write_text(data["token"])
    DEVICE_TOKEN_FILE.chmod(0o640)
    write_device_name(device_name)
    if data.get("entity_name"):
        write_entity_name(data["entity_name"])
    return data


def unpair_and_wipe() -> None:
    """Deletes the token and cached slide/settings state. Does not itself
    reboot — callers decide that (the local Settings UI's explicit unpair
    action reboots via system_control.reboot() right after; heartbeat.py's
    401 handling does the same).
    """
    for path in WIPE_PATHS:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
