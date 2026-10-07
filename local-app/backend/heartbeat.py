"""Periodic heartbeat client — POSTs device metrics to the server every 5
minutes (see SLIDE_ANNOUNCER.md, "Heartbeat + version checks") and folds
the response's update-availability fields into a local status file. Runs
as a background asyncio task inside this backend (started from main.py's
lifespan), not a separate systemd timer — heartbeat delivery doesn't need
root and doesn't need to survive a backend crash independently of the rest
of this process.

A network-level failure (timeout, DNS, connection refused) is recorded in
the status file and otherwise ignored — nothing else to do yet, since the
slide sync daemon (the thing whose cache this would protect) is still a
stub. A 401 is different in kind: it means the *server* was reached and
explicitly rejected this device's token (revoked/unpaired from the
website), which triggers the same wipe-and-reboot path an explicit local
unpair uses — see pairing.py and SLIDE_ANNOUNCER.md's Heartbeat/revocation
section.
"""
import asyncio
import json
import platform
import socket
from datetime import datetime, timezone
from pathlib import Path

import httpx

import pairing
import product
import system_control

INTERVAL_SECONDS = 5 * 60

# Request keys the core heartbeat owns; a product's heartbeat_payload() may
# not override them.
_CORE_KEYS = {"app_version", "os_version", "architecture", "cpu_temp_c", "hostname", "language_change"}

OS_VERSION_FILE = Path("/opt/slide-announcer/VERSION")
APP_VERSION_FILE = Path("/data/local-app/current/VERSION")
CPU_TEMP_FILE = Path("/sys/class/thermal/thermal_zone0/temp")
STATUS_FILE = Path("/data/status/heartbeat.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_os_version() -> str | None:
    if OS_VERSION_FILE.exists():
        return OS_VERSION_FILE.read_text().strip()
    return None


def read_app_version() -> str | None:
    if APP_VERSION_FILE.exists():
        return APP_VERSION_FILE.read_text().strip()
    return None


def read_architecture() -> str:
    """`platform.machine()` (e.g. 'aarch64' on 64-bit Raspberry Pi OS,
    'armv7l' on 32-bit) — reported verbatim, not mapped to a fixed name,
    since the server's architecture field is a free-form string (see
    SlideAnnouncerRelease::KINDS/CHANNELS vs. architecture having no
    equivalent constant). Whatever this returns is exactly the string an
    admin needs to type into the release-publish form's Architecture
    field for a build to match this device.
    """
    return platform.machine()


def read_cpu_temp_c() -> float | None:
    """Raspberry Pi's SoC thermal zone — reads in millidegrees C."""
    if not CPU_TEMP_FILE.exists():
        return None
    try:
        return int(CPU_TEMP_FILE.read_text().strip()) / 1000
    except ValueError:
        return None


def _write_status(data: dict) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(data))
    # Explicit chmod, not just this process's umask — the same lesson
    # local-app-seed.py and update-check.py's status files already apply:
    # readers of this file (the About screen, via GET /api/local/status)
    # aren't necessarily the same user/process that wrote it.
    STATUS_FILE.chmod(0o644)


def read_status() -> dict | None:
    if not STATUS_FILE.exists():
        return None
    try:
        return json.loads(STATUS_FILE.read_text())
    except json.JSONDecodeError:
        return None


async def send_once() -> None:
    """Sends one heartbeat. A no-op if unpaired — nothing to report yet."""
    token = pairing.read_device_token()
    if not token:
        return

    payload = {
        "app_version": read_app_version(),
        "os_version": read_os_version(),
        "architecture": read_architecture(),
        "cpu_temp_c": read_cpu_temp_c(),
        # Reported every heartbeat (cheap, and can change on a
        # slideannouncer.yaml hostname override + reboot) so the management
        # UI can show how to reach this device on its LAN.
        "hostname": socket.gethostname(),
        # Product data riding on the core heartbeat (docs/DEVICE_CONTRACT.md,
        # "Extensions") — core keys above always win a name collision.
        **{k: v for k, v in product.get().heartbeat_payload().items() if k not in _CORE_KEYS},
    }

    # A language picked on this device and not yet acknowledged — sent with
    # the server revision it was picked against, so the server can tell it
    # apart from a web edit made in the meantime (that one wins).
    sent_language = pairing.read_pending_language()
    if sent_language:
        payload["language_change"] = {
            "code": sent_language,
            "base_revision": pairing.read_language_revision(),
        }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                pairing.api_url("heartbeat"),
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.RequestError as exc:
        _write_status({"last_attempt_at": _now_iso(), "last_error": str(exc)})
        return

    if resp.status_code == 401:
        pairing.unpair_and_wipe()
        _write_status({"last_attempt_at": _now_iso(), "last_error": "revoked"})
        await system_control.reboot()
        return

    if resp.status_code >= 400:
        _write_status({"last_attempt_at": _now_iso(), "last_error": f"HTTP {resp.status_code}"})
        return

    response = resp.json()

    # The server is authoritative for this device's name once paired (an
    # entity admin can rename it from the fleet UI) — every successful
    # heartbeat folds whatever it reports back into the local cache
    # pairing.py's pair() first seeded, so a rename shows up here within
    # one heartbeat interval without the device needing a rename API of
    # its own.
    if response.get("device_name"):
        pairing.write_device_name(response["device_name"])
    # Same sync, for the entity (church/school) this device is paired to —
    # also covers a re-pair moving it to a different entity, which changes
    # this without touching device_name at all.
    if response.get("entity_name"):
        pairing.write_entity_name(response["entity_name"])
    # Language is one two-way setting: a web edit lands here, and a pick made
    # on this device went up in `language_change` above. Either way the
    # server's answer is the settled value — a stale device pick loses to a
    # newer web edit. `language` is null until someone has set one, in which
    # case the boot-yaml hint keeps applying (pairing.read_effective_language()).
    pairing.apply_server_language(
        response.get("language"), response.get("language_revision"), sent_language
    )
    # Whatever the product keeps in sync through the heartbeat.
    product.get().on_heartbeat_response(response)

    _write_status({
        "last_attempt_at": _now_iso(),
        "last_success_at": _now_iso(),
        "last_error": None,
        "response": response,
    })


async def run_forever() -> None:
    while True:
        try:
            await send_once()
        except Exception as exc:  # a bug here must never take the backend down
            _write_status({"last_attempt_at": _now_iso(), "last_error": f"unexpected: {exc}"})
        await asyncio.sleep(INTERVAL_SECONDS)
