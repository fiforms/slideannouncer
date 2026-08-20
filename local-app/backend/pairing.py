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
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

import identity
import pinning

BOOT_YAML = Path("/boot/firmware/slideannouncer.yaml")
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
# The device's actual (mDNS) hostname, derived from device_name at pairing
# time — see slugify_hostname() and pair() below. Deliberately a separate
# file from DEVICE_NAME_FILE rather than re-slugifying it on every boot:
# the slug written here has already been checked against sibling_hostnames
# (see pair()), and a name that collided once shouldn't silently collide
# again after some other device's name changes later. firstboot.py's
# set_hostname() reads this file every boot and applies it (falling back to
# a device_uuid-derived numeric name pre-pairing) — this module only ever
# writes it, never applies it directly, since actually renaming the OS host
# needs root, which this backend deliberately doesn't run as.
HOSTNAME_FILE = Path("/data/status/hostname")
# Hostnames only use these characters (RFC1123-ish, plus underscore since
# that's what an operator naturally expects from converting spaces) —
# anything else in a typed device name is just dropped rather than mapped
# to a placeholder, so punctuation-heavy names still collapse to something
# readable instead of a run of substitute characters.
_HOSTNAME_ILLEGAL_CHARS = re.compile(r"[^a-z0-9_-]")
_HOSTNAME_WHITESPACE = re.compile(r"\s+")
# DNS label length limit (RFC 1035) minus room for ".local" and a possible
# "-NN" collision suffix.
_HOSTNAME_MAX_LEN = 40
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
# Which physical output PipeWire should default to — "hdmi" (the TV, via
# the same cable driving the display) or "headphones" (the Pi's analogue
# jack, e.g. feeding a church PA). Purely a device-local hardware
# preference, set from the Settings screen — see AUDIO_IMPLEMENTATION.md
# for the full design. Deliberately NOT in WIPE_PATHS below, since it
# describes how this device is wired into the room, not anything about
# its pairing.
AUDIO_OUTPUT_FILE = Path("/data/status/audio-output")
DEFAULT_AUDIO_OUTPUT = "hdmi"
# Current volume (0-100) and mute state — written by the remote's
# volume/mute keys (system/scripts/volume-key-monitor.py, a standalone
# process, not this backend) and re-applied at boot/output-switch time by
# apply-audio-output.sh. This module only reads them, for the
# /api/local/audio-volume endpoint the kiosk page polls to reflect
# changes on screen. Same not-in-WIPE_PATHS reasoning as
# AUDIO_OUTPUT_FILE — a volume level is a property of the room, not of
# this device's pairing state.
AUDIO_VOLUME_FILE = Path("/data/status/audio-volume")
AUDIO_MUTED_FILE = Path("/data/status/audio-muted")
DEFAULT_AUDIO_VOLUME = 100

# Wiped together, always — see this module's docstring for the three
# triggers that share this list (explicit unpair, 401 revocation, and
# provisioning.py's own identity-mismatch wipe, which duplicates this list
# rather than importing it since that script runs standalone, as root,
# before this backend exists).
WIPE_PATHS = [
    DEVICE_TOKEN_FILE,
    DEVICE_NAME_FILE,
    HOSTNAME_FILE,
    ENTITY_NAME_FILE,
    LANGUAGE_FILE,
    Path("/data/slides"),
    Path("/data/local-app/settings.json"),
    # A pinned show id is meaningless once unpaired — a re-pair may attach
    # this device to a different entity's show catalog entirely, so it's
    # wiped with the rest of the pairing state, unlike the room-property
    # settings (audio-output, audio-volume) above.
    pinning.PINNED_SHOW_ID_FILE,
]


class PairingError(RuntimeError):
    """Raised with a message safe to show directly on the pairing screen."""


def read_server_url() -> str:
    """The AnnouncementSlides server this device talks to — a `server_url`
    scalar in /boot/firmware/slideannouncer.yaml (see that file's own
    comment), not a build-time constant, so the same image can serve
    multiple independent servers just by swapping this file. Fails closed
    (same spirit as ssh-gate.py) rather than silently doing nothing.
    """
    if not BOOT_YAML.exists():
        raise PairingError(f"{BOOT_YAML} is missing — this device has no boot config at all.")
    data = yaml.safe_load(BOOT_YAML.read_text()) or {}
    server_url = data.get("server_url")
    if not server_url:
        raise PairingError(f"server_url is not set in {BOOT_YAML}.")
    return server_url.rstrip("/")


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


def read_hostname() -> str | None:
    if not HOSTNAME_FILE.exists():
        return None
    return HOSTNAME_FILE.read_text().strip() or None


def write_hostname(hostname: str) -> None:
    HOSTNAME_FILE.parent.mkdir(parents=True, exist_ok=True)
    HOSTNAME_FILE.write_text(hostname)
    HOSTNAME_FILE.chmod(0o644)


def slugify_hostname(name: str) -> str:
    """"SlideAnnouncer-1234" becomes "slideannouncer-1234"; "My dog is Funny!!!"
    -> "my_dog_is_funny". Lowercases, collapses whitespace to underscores,
    drops anything else that isn't a hostname-safe character, then trims
    stray leading/trailing separators left over from that and caps the
    length. Can return "" for a name that's nothing but punctuation/emoji —
    callers fall back to a device_uuid-derived name in that case (see
    pair() below), same as an entirely blank name would.
    """
    slug = _HOSTNAME_WHITESPACE.sub("_", name.strip().lower())
    slug = _HOSTNAME_ILLEGAL_CHARS.sub("", slug)
    slug = slug.strip("_-")[:_HOSTNAME_MAX_LEN]
    return slug.strip("_-")


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


def read_audio_output() -> str:
    if not AUDIO_OUTPUT_FILE.exists():
        return DEFAULT_AUDIO_OUTPUT
    value = AUDIO_OUTPUT_FILE.read_text().strip()
    return value if value in ("hdmi", "headphones") else DEFAULT_AUDIO_OUTPUT


def write_audio_output(value: str) -> None:
    AUDIO_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUDIO_OUTPUT_FILE.write_text(value)
    AUDIO_OUTPUT_FILE.chmod(0o644)


def read_audio_volume() -> int:
    if not AUDIO_VOLUME_FILE.exists():
        return DEFAULT_AUDIO_VOLUME
    try:
        value = int(AUDIO_VOLUME_FILE.read_text().strip())
    except ValueError:
        return DEFAULT_AUDIO_VOLUME
    return max(0, min(100, value))


def read_audio_muted() -> bool:
    if not AUDIO_MUTED_FILE.exists():
        return False
    return AUDIO_MUTED_FILE.read_text().strip() == "true"


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
    language_hint = read_language_boot_hint()
    if language_hint:
        payload["language"] = language_hint

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

    # Derive this device's hostname from the name just typed, picking
    # around any sibling already using that name on this entity — see
    # slugify_hostname()'s docstring and the pairing endpoint's
    # sibling_hostnames comment. Takes effect on the next reboot, same as
    # a hand-set `hostname` in slideannouncer.yaml always has (see
    # firstboot.py's set_hostname()).
    device_uuid = identity.get_device_uuid()
    slug = slugify_hostname(device_name) or (
        identity.derive_numeric_hostname(device_uuid) if device_uuid else "slideannouncer"
    )
    taken = set(data.get("sibling_hostnames") or [])
    hostname, suffix = slug, 2
    while hostname in taken:
        hostname = f"{slug}-{suffix}"
        suffix += 1
    write_hostname(hostname)

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
