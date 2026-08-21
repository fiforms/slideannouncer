"""Discovery + pairing client for Revelation Snapshot Presenter "peering" —
see doc/dev/PEERING.md in fiforms/revelation-electron-wrapper for the wire
protocol this follows. This device only ever acts as a "follower": a
Revelation "master" on the LAN pairs are always *initiated* from the
follower per that doc, so this module does the mDNS scan + HTTP PIN
challenge-response and persists the resulting trust record.

The live command channel (Socket.IO connection to each paired master,
listening for `peer-command` events, and driving the kiosk's already-running
Chromium via its remote-debugging port) is deliberately NOT here — it's
system/scripts/revelation-peer-daemon.py, a separate always-on systemd unit
in the same spirit as srt-sink-monitor.py, reading the trust file this
module writes rather than importing it (this module lives in the versioned
local-app release; the daemon is fixed OS-image infra — see that script's
own docstring). That daemon also duplicates the small signature-verification
helper below rather than import across that boundary, same reasoning as
srt_sink.py's effective_enabled() being duplicated in srt-sink-monitor.py.

Trust and status are two separate files, same split as srt_sink.py/
pairing.py already use elsewhere: REVELATION_PEERS_FILE holds the actual
trust records (paired master's pinned public key + pairing PIN — a secret,
hence 0o640) and is only ever written by this module's pair()/unpair();
REVELATION_STATUS_FILE holds the daemon's own live connection state
(connected/last-command per paired master) and is only ever written by the
daemon, polled by /api/local/revelation/status for the Settings UI.
"""
import base64
import json
import secrets
import socket as socket_module
import time
from pathlib import Path

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

# The doc's "Service type: revelation" is bonjour-service/mdns shorthand —
# the actual wire service type it publishes is the standard
# "_<name>._tcp.local." form.
MDNS_SERVICE_TYPE = "_revelation._tcp.local."
DISCOVERY_TIMEOUT_SECONDS = 4.0

INSTANCE_ID_FILE = Path("/data/status/revelation-instance-id")
REVELATION_PEERS_FILE = Path("/data/status/revelation-peers.json")
REVELATION_STATUS_FILE = Path("/data/status/revelation-peer-status.json")
# Device-global (not per-master) — how this follower should present
# whatever Revelation pushes it, independent of which master sent it. Read
# by revelation-peer-daemon.py on every open-presentation command (see that
# script's own copy of these constants/apply_display_settings()) rather
# than per-peer, since a church running one follower per room wants "this
# room always shows lower-thirds" regardless of who's presenting.
REVELATION_DISPLAY_SETTINGS_FILE = Path("/data/status/revelation-display-settings.json")
# Revelation's own known ?variant= values (see its Snapshot Presenter
# peering UI) — anything else is rejected rather than silently forwarded,
# since a typo'd variant would otherwise only surface as a confusing
# no-visible-effect URL param on the kiosk.
VALID_VARIANTS = {"normal", "notes", "confidence", "lowerthirds"}


class RevelationPeerError(RuntimeError):
    """Raised with a message safe to show directly on the Settings screen."""


def get_own_instance_id() -> str:
    """This device's own stable instanceId, in the same 16-hex-char/8-random-
    byte shape the protocol doc uses for Revelation's own instanceId — sent
    as the `instanceId` query param on /peer/socket-info and in the
    Socket.IO auth payload so a master can tell this follower apart from any
    other paired follower."""
    if INSTANCE_ID_FILE.exists():
        return INSTANCE_ID_FILE.read_text().strip()
    instance_id = secrets.token_hex(8)
    INSTANCE_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    INSTANCE_ID_FILE.write_text(instance_id)
    INSTANCE_ID_FILE.chmod(0o644)
    return instance_id


class _DiscoveryListener(ServiceListener):
    def __init__(self):
        self.found = {}

    def add_service(self, zc, service_type, name):
        info = zc.get_service_info(service_type, name)
        if info is None or not info.addresses:
            return
        txt = {
            key.decode(): value.decode() if value is not None else None
            for key, value in (info.properties or {}).items()
        }
        self.found[name] = {
            "name": name,
            "host": socket_module.inet_ntoa(info.addresses[0]),
            "port": info.port,
            "instanceId": txt.get("instanceId"),
            "mode": txt.get("mode"),
            "version": txt.get("version"),
            "hostname": txt.get("hostname"),
            "pubKeyFingerprint": txt.get("pubKeyFingerprint"),
        }

    def update_service(self, zc, service_type, name):
        self.add_service(zc, service_type, name)

    def remove_service(self, zc, service_type, name):
        self.found.pop(name, None)


def discover(timeout: float = DISCOVERY_TIMEOUT_SECONDS) -> list[dict]:
    """Browses mDNS for `timeout` seconds and returns whatever Revelation
    masters answered. Synchronous/blocking by design (python-zeroconf's
    ServiceBrowser runs its own background thread regardless) — callers on
    the async side (main.py's /api/local/revelation/scan) run this via
    asyncio.to_thread so it doesn't block the event loop."""
    zc = Zeroconf()
    listener = _DiscoveryListener()
    browser = ServiceBrowser(zc, MDNS_SERVICE_TYPE, listener)
    try:
        time.sleep(timeout)
    finally:
        browser.cancel()
        zc.close()
    return list(listener.found.values())


def verify_signature(public_key_pem: str, message: bytes, signature_b64: str) -> bool:
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode())
        public_key.verify(
            base64.b64decode(signature_b64),
            message,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except (InvalidSignature, ValueError):
        return False


def _read_raw_peers() -> list[dict]:
    if not REVELATION_PEERS_FILE.exists():
        return []
    try:
        return json.loads(REVELATION_PEERS_FILE.read_text())
    except json.JSONDecodeError:
        return []


def _write_raw_peers(peers: list[dict]) -> None:
    REVELATION_PEERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    REVELATION_PEERS_FILE.write_text(json.dumps(peers))
    REVELATION_PEERS_FILE.chmod(0o640)


def read_peers() -> list[dict]:
    """Trust records with `pairingPin` stripped — safe to hand straight to
    the Settings UI, which never needs to see a PIN it already typed once."""
    return [{k: v for k, v in peer.items() if k != "pairingPin"} for peer in _read_raw_peers()]


def read_status() -> dict:
    """Live connection state, if the daemon has written any yet — merged
    onto the trust list for the Settings screen. Missing/unreadable status
    file just means "daemon hasn't reported in yet", not an error."""
    connections = {}
    if REVELATION_STATUS_FILE.exists():
        try:
            connections = json.loads(REVELATION_STATUS_FILE.read_text()).get("connections", {})
        except json.JSONDecodeError:
            pass
    peers = read_peers()
    for peer in peers:
        peer["connection"] = connections.get(peer["instanceId"])
    return {"peers": peers}


async def pair(host: str, port: int, pin: str) -> dict:
    """Runs the full PEERING.md pairing flow against a candidate master and,
    on success, pins its public key into the trust store. Raises
    RevelationPeerError with a message safe to show as-is."""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            identity_resp = await client.get(f"http://{host}:{port}/peer/public-key")
        except httpx.RequestError as exc:
            raise RevelationPeerError(f"Could not reach {host}:{port}: {exc}") from exc
        if identity_resp.status_code >= 400:
            raise RevelationPeerError(f"{host}:{port} rejected the identity request (HTTP {identity_resp.status_code}).")
        identity = identity_resp.json()

        challenge_bytes = secrets.token_bytes(32)
        challenge_b64 = base64.b64encode(challenge_bytes).decode()
        try:
            challenge_resp = await client.post(
                f"http://{host}:{port}/peer/challenge",
                json={"challenge": challenge_b64, "pin": pin},
            )
        except httpx.RequestError as exc:
            raise RevelationPeerError(f"Could not reach {host}:{port}: {exc}") from exc

    if challenge_resp.status_code == 403:
        raise RevelationPeerError("Incorrect pairing PIN.")
    if challenge_resp.status_code >= 400:
        raise RevelationPeerError(f"Pairing failed (master said HTTP {challenge_resp.status_code}).")

    signature = challenge_resp.json().get("signature", "")
    # Signed over the exact `challenge` field value (the base64 string
    # itself, UTF-8 encoded) — matches the doc's later socket-info payload,
    # which is also a signed string tuple rather than raw decoded bytes.
    if not verify_signature(identity["publicKey"], challenge_b64.encode(), signature):
        raise RevelationPeerError(f"{host}:{port} failed to prove its identity — refusing to pair.")

    peers = _read_raw_peers()
    peers = [peer for peer in peers if peer["instanceId"] != identity["instanceId"]]
    peers.append({
        "instanceId": identity["instanceId"],
        "name": identity.get("instanceName") or identity.get("hostname") or identity["instanceId"],
        "publicKey": identity["publicKey"],
        "pairedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hostHint": host,
        "pairingPortHint": port,
        "pairingPin": pin,
    })
    _write_raw_peers(peers)

    return {"instanceId": identity["instanceId"], "name": identity.get("instanceName")}


def unpair(instance_id: str) -> None:
    peers = [peer for peer in _read_raw_peers() if peer["instanceId"] != instance_id]
    _write_raw_peers(peers)


def read_display_settings() -> dict:
    """{"variant": None, "lang": None} means "don't touch the URL Revelation
    sent" — see the daemon's apply_display_settings()."""
    if not REVELATION_DISPLAY_SETTINGS_FILE.exists():
        return {"variant": None, "lang": None}
    try:
        data = json.loads(REVELATION_DISPLAY_SETTINGS_FILE.read_text())
    except json.JSONDecodeError:
        return {"variant": None, "lang": None}
    return {"variant": data.get("variant"), "lang": data.get("lang")}


def write_display_settings(variant: str | None, lang: str | None) -> dict:
    if variant is not None and variant not in VALID_VARIANTS:
        raise RevelationPeerError(f"Unknown variant {variant!r}.")
    settings = {"variant": variant or None, "lang": lang or None}
    REVELATION_DISPLAY_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    REVELATION_DISPLAY_SETTINGS_FILE.write_text(json.dumps(settings))
    REVELATION_DISPLAY_SETTINGS_FILE.chmod(0o644)
    return settings
