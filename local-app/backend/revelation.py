"""Discovery + pairing client for Revelation Snapshot Presenter "peering" —
see doc/dev/PEERING.md in fiforms/revelation-electron-wrapper for the wire
protocol this follows. This device only ever acts as a "follower": a
Revelation "master" on the LAN pairs are always *initiated* from the
follower per that doc, so this module does the mDNS scan + HTTP PIN
challenge-response and persists the resulting trust record.

The live command channel (Socket.IO connection to each paired master,
listening for `peer-command` events, and driving the kiosk's already-running
Chromium via its remote-debugging port) is deliberately NOT here — it's
system/scripts/revelation-peer-daemon.py, a separate always-on systemd
unit, reading the trust file this module writes rather than importing it
(this module lives in the versioned local-app release; the daemon is
fixed OS-image infra — see that script's own docstring). That daemon also
duplicates the small signature-verification helper below rather than
import across that boundary, for the same reason — a standalone
OS-image script can't import from a module that ships in the versioned
local-app release.

Trust and status are two separate files, same split as srt_sink.py/
pairing.py already use elsewhere: REVELATION_PEERS_FILE holds the actual
trust records (paired master's pinned public key — no PIN, it is used once
at enrollment and never stored) and is only ever written by this module's
pair()/unpair(); this device's own peer RSA private key lives beside it in
PEER_PRIVATE_KEY_FILE (a secret, hence 0o640);
REVELATION_STATUS_FILE holds the daemon's own live connection state
(connected/last-command per paired master) and is only ever written by the
daemon, polled by /api/local/revelation/status for the Settings UI.
"""
import base64
import hashlib
import json
import secrets
import socket as socket_module
import time
from pathlib import Path

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

# The doc's "Service type: revelation" is bonjour-service/mdns shorthand —
# the actual wire service type it publishes is the standard
# "_<name>._tcp.local." form.
MDNS_SERVICE_TYPE = "_revelation._tcp.local."
DISCOVERY_TIMEOUT_SECONDS = 4.0

INSTANCE_ID_FILE = Path("/data/status/revelation-instance-id")
REVELATION_PEERS_FILE = Path("/data/status/revelation-peers.json")
REVELATION_STATUS_FILE = Path("/data/status/revelation-peer-status.json")
# This follower's own peer RSA key pair (protocol 2: identity is
# cryptographic in both directions, so every request after pairing is signed
# with this key). Generated once, on first pairing; read by the daemon.
PEER_PRIVATE_KEY_FILE = Path("/data/status/revelation-peer-key.pem")
PEER_PUBLIC_KEY_FILE = Path("/data/status/revelation-peer-key.pub.pem")
PEER_PROTOCOL = 2
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
VALID_VARIANTS = {
    "normal", "lowerthirds", "confidence", "notes", "remotepreview", "notesteleprompter",
}


class RevelationPeerError(RuntimeError):
    """Raised with a message safe to show directly on the Settings screen."""


def get_own_instance_id() -> str:
    """This device's own stable instanceId, in the same 16-hex-char/8-random-
    byte shape the protocol doc uses for Revelation's own instanceId — sent
    as `followerInstanceId` on every signed request so a master can tell this
    follower apart from any other paired follower."""
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
            "port": int(txt.get("pairingPort") or info.port),
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


def get_or_create_keypair() -> tuple[str, str]:
    """(private PEM, public PEM) of this follower's own peer key. RSA-2048 —
    the protocol's minimum."""
    if PEER_PRIVATE_KEY_FILE.exists() and PEER_PUBLIC_KEY_FILE.exists():
        return PEER_PRIVATE_KEY_FILE.read_text(), PEER_PUBLIC_KEY_FILE.read_text()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    PEER_PRIVATE_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    PEER_PRIVATE_KEY_FILE.write_text(private_pem)
    PEER_PRIVATE_KEY_FILE.chmod(0o640)
    PEER_PUBLIC_KEY_FILE.write_text(public_pem)
    PEER_PUBLIC_KEY_FILE.chmod(0o644)
    return private_pem, public_pem


def challenge_message(challenge: str) -> bytes:
    """What a master signs to prove itself: domain prefix + sha256 hex of the
    challenge (the base64 string itself, UTF-8 encoded)."""
    digest = hashlib.sha256(challenge.encode()).hexdigest()
    return f"revelation-peer-challenge:v1:{digest}".encode()


def follower_auth_message(purpose: str, master_id: str, follower_id: str, nonce: str, extra: str = "") -> bytes:
    digest = hashlib.sha256(f"{purpose}\n{master_id}\n{follower_id}\n{nonce}\n{extra}".encode()).hexdigest()
    return f"revelation-peer-follower-auth:v2:{digest}".encode()


def sign_message(private_key_pem: str, message: bytes) -> str:
    private_key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    return base64.b64encode(private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode()


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
    """Trust records for the Settings UI. A record from before protocol 2
    (no `peerProtocol`, and carrying a stored PIN) can't authenticate any
    more, so it is surfaced as `needsRepair` rather than hidden."""
    peers = []
    for peer in _read_raw_peers():
        peer = {k: v for k, v in peer.items() if k != "pairingPin"}
        if peer.get("peerProtocol") != PEER_PROTOCOL:
            peer["needsRepair"] = True
        peers.append(peer)
    return peers


def read_status() -> dict:
    """Live connection state, if the daemon has written any yet — merged
    onto the trust list for the Settings screen. Missing/unreadable status
    file just means "daemon hasn't reported in yet", not an error. The
    daemon also flags `needsRepair` there when a master says it no longer
    knows this follower."""
    connections = {}
    if REVELATION_STATUS_FILE.exists():
        try:
            connections = json.loads(REVELATION_STATUS_FILE.read_text()).get("connections", {})
        except json.JSONDecodeError:
            pass
    peers = read_peers()
    for peer in peers:
        peer["connection"] = connections.get(peer["instanceId"])
        if (peer["connection"] or {}).get("needsRepair"):
            peer["needsRepair"] = True
    return {"peers": peers}


def _error_code(resp: httpx.Response) -> str | None:
    try:
        return resp.json().get("code")
    except (ValueError, AttributeError):
        return None


async def pair(host: str, port: int, pin: str) -> dict:
    """Runs the full PEERING.md (protocol 2) pairing flow against a candidate
    master and, on success, pins its public key into the trust store. The
    PIN is sent to /peer/pair only and never persisted. Raises
    RevelationPeerError with a message safe to show as-is."""
    base = f"http://{host}:{port}"
    _, public_pem = get_or_create_keypair()
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            identity_resp = await client.get(f"{base}/peer/public-key")
        except httpx.RequestError as exc:
            raise RevelationPeerError(f"Could not reach {host}:{port}: {exc}") from exc
        if identity_resp.status_code >= 400:
            raise RevelationPeerError(f"{host}:{port} rejected the identity request (HTTP {identity_resp.status_code}).")
        identity = identity_resp.json()
        if identity.get("peerProtocol") != PEER_PROTOCOL:
            raise RevelationPeerError(
                f"{host}:{port} speaks an incompatible peering protocol — update Revelation Snapshot Presenter there."
            )

        own_id = get_own_instance_id()
        challenge_b64 = base64.b64encode(secrets.token_bytes(32)).decode()
        try:
            pair_resp = await client.post(
                f"{base}/peer/pair",
                json={
                    "pin": pin,
                    "challenge": challenge_b64,
                    "followerInstanceId": own_id,
                    "followerName": socket_module.gethostname(),
                    "followerPublicKey": public_pem,
                },
            )
        except httpx.RequestError as exc:
            raise RevelationPeerError(f"Could not reach {host}:{port}: {exc}") from exc

    if pair_resp.status_code >= 400:
        code = _error_code(pair_resp)
        if code == "invalid-pin":
            raise RevelationPeerError("Incorrect pairing PIN.")
        if code == "pin-lockout":
            retry = pair_resp.json().get("retryAfterSec")
            raise RevelationPeerError(
                f"Too many wrong PINs — try again in {retry} seconds." if retry else "Too many wrong PINs — try again shortly."
            )
        if code == "pairing-unavailable":
            raise RevelationPeerError("That Revelation instance has no pairing PIN configured.")
        raise RevelationPeerError(f"Pairing failed (master said HTTP {pair_resp.status_code}).")

    # Prefer an already-pinned key for this master over the freshly fetched one.
    pinned = next((p for p in _read_raw_peers() if p["instanceId"] == identity["instanceId"]), None)
    verify_key = (pinned or {}).get("publicKey") or identity["publicKey"]
    signature = pair_resp.json().get("signature", "")
    if not verify_signature(verify_key, challenge_message(challenge_b64), signature):
        raise RevelationPeerError(f"{host}:{port} failed to prove its identity — refusing to pair.")

    peers = [peer for peer in _read_raw_peers() if peer["instanceId"] != identity["instanceId"]]
    peers.append({
        "instanceId": identity["instanceId"],
        "name": identity.get("instanceName") or identity.get("hostname") or identity["instanceId"],
        "peerPublicKey": verify_key,
        "peerProtocol": PEER_PROTOCOL,
        "pairedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hostHint": host,
        "pairingPortHint": port,
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
