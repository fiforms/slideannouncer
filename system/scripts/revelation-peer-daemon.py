#!/usr/bin/env python3
"""Revelation Snapshot Presenter peering — the live command channel.

Companion to local-app/backend/revelation.py, which owns discovery/pairing
(driven by the Settings > Revelation Peering UI) and writes the trust store
this daemon only ever reads: /data/status/revelation-peers.json (paired
masters' pinned public keys + pairing PINs). This script is fixed OS-image
infra (installed to /usr/local/sbin, its own always-on systemd unit — see
slide-announcer-revelation-peer.service), independent of whichever
local-app release happens to be current, same reasoning as
local_app_updater.py. It duplicates the small signature-verification helper
from revelation.py rather than import across that boundary, same pattern
srt-sink-monitor.py already uses for srt_sink.py's effective_enabled().

For each paired master, this maintains a persistent Socket.IO connection
(one worker thread per master — see peer_worker()) and reacts to
`peer-command` events by driving the kiosk's already-running Chromium
through its remote-debugging port (see kiosk-start.sh's
--remote-debugging-port=9222): `open-presentation` navigates the kiosk tab
to the given URL, `close-presentation` navigates it back to the kiosk's own
page. The kiosk unit itself is never touched — unlike display-power.py's
`takeover` for the SRT sink, there's no need to stop it, since CDP can
retarget an already-running tab in place.

mDNS discovery here exists only to refresh a paired master's current host/
port (it may have moved to a different DHCP lease since pairing) and to
re-verify it's still listening before reconnecting — see the protocol doc's
"Re-verified on mDNS `up`" note. It does NOT drive who this device is
paired with; that's entirely the trust store's job.
"""
import base64
import json
import secrets
import socket
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import socketio
import websocket
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

MDNS_SERVICE_TYPE = "_revelation._tcp.local."
REVELATION_PEERS_FILE = Path("/data/status/revelation-peers.json")
REVELATION_STATUS_FILE = Path("/data/status/revelation-peer-status.json")
INSTANCE_ID_FILE = Path("/data/status/revelation-instance-id")
# Device-global, written by local-app/backend/revelation.py's Settings UI
# endpoints — duplicated here rather than imported, same reasoning as the
# signature-verification helper below.
REVELATION_DISPLAY_SETTINGS_FILE = Path("/data/status/revelation-display-settings.json")

KIOSK_URL = "http://localhost/kiosk"
CDP_PORT = 9222

TRUST_POLL_SECONDS = 10.0
RECONNECT_BACKOFF_SECONDS = 5.0
HTTP_TIMEOUT_SECONDS = 10.0


def log(message: str) -> None:
    print(f"[revelation-peer] {message}", flush=True)


def get_own_instance_id() -> str:
    if INSTANCE_ID_FILE.exists():
        return INSTANCE_ID_FILE.read_text().strip()
    # Set by local-app/backend/revelation.py on first Settings-UI scan in
    # the normal case — this fallback only matters if the daemon starts
    # before that has ever happened.
    instance_id = secrets.token_hex(8)
    INSTANCE_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    INSTANCE_ID_FILE.write_text(instance_id)
    return instance_id


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


def read_peers() -> list[dict]:
    if not REVELATION_PEERS_FILE.exists():
        return []
    try:
        return json.loads(REVELATION_PEERS_FILE.read_text())
    except json.JSONDecodeError:
        return []


class _DiscoveryListener(ServiceListener):
    """Keeps a live instanceId -> (host, port) map, refreshed continuously —
    the trust store's hostHint/pairingPortHint are only ever a fallback for
    a master this hasn't seen advertise since the daemon started."""

    def __init__(self):
        self.lock = threading.Lock()
        self.by_instance_id = {}

    def add_service(self, zc, service_type, name):
        info = zc.get_service_info(service_type, name)
        if info is None or not info.addresses:
            return
        txt = {
            key.decode(): value.decode() if value is not None else None
            for key, value in (info.properties or {}).items()
        }
        instance_id = txt.get("instanceId")
        if not instance_id:
            return
        with self.lock:
            self.by_instance_id[instance_id] = (socket.inet_ntoa(info.addresses[0]), info.port)

    def update_service(self, zc, service_type, name):
        self.add_service(zc, service_type, name)

    def remove_service(self, zc, service_type, name):
        pass  # stale entries just fall back to hostHint below, no need to chase removals

    def get(self, instance_id):
        with self.lock:
            return self.by_instance_id.get(instance_id)


def write_status(connections: dict) -> None:
    REVELATION_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    REVELATION_STATUS_FILE.write_text(json.dumps({"connections": connections}))


def _cdp_read_response(ws, command_id: int, max_messages: int = 10) -> dict | None:
    """Reads frames off `ws` until one whose "id" matches `command_id`
    arrives, skipping unrelated CDP events (e.g. Target.targetInfoChanged)
    that can interleave on the shared browser-level socket. Bounded by
    `max_messages` rather than only ws's own timeout, so a chatty target
    can't stall the caller past that many frames."""
    for _ in range(max_messages):
        try:
            message = json.loads(ws.recv())
        except Exception:
            return None
        if message.get("id") == command_id:
            return message
    return None


def cdp_navigate(url: str) -> None:
    """Redirects the kiosk's already-running Chromium tab via the Chrome
    DevTools Protocol — see kiosk-start.sh's --remote-debugging-port.

    Connecting directly to a page target's own webSocketDebuggerUrl (the
    naive approach) was tried and confirmed broken on hardware against
    Chrome 151: that target's own listing shows "attached": false and it
    never responds to *any* command, not just Page.navigate — Target.getTargets
    against it, and even Page.enable, both drop into a black hole. Only the
    browser-level socket (from /json/version) ever gets or sends anything.
    The fix is the "flattened" session protocol: Target.attachToTarget on
    the browser socket to get a sessionId, then tag every subsequent
    command for that target with `sessionId` on that same connection —
    this is also how the real Chrome DevTools front-end itself talks to a
    target now, not a workaround specific to this codebase.
    """
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
        browser_info = client.get(f"http://127.0.0.1:{CDP_PORT}/json/version").json()
        targets = client.get(f"http://127.0.0.1:{CDP_PORT}/json").json()
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        log("cdp_navigate: no page target found on the kiosk's debug port")
        return

    ws = websocket.create_connection(browser_info["webSocketDebuggerUrl"], timeout=HTTP_TIMEOUT_SECONDS)
    try:
        ws.send(json.dumps({
            "id": 1,
            "method": "Target.attachToTarget",
            "params": {"targetId": pages[0]["id"], "flatten": True},
        }))
        attach_resp = _cdp_read_response(ws, 1)
        session_id = (attach_resp or {}).get("result", {}).get("sessionId")
        if not session_id:
            log(f"cdp_navigate: Target.attachToTarget failed: {attach_resp}")
            return

        ws.send(json.dumps({
            "id": 2,
            "method": "Page.navigate",
            "params": {"url": url},
            "sessionId": session_id,
        }))
        navigate_resp = _cdp_read_response(ws, 2)
        if navigate_resp is None or "error" in navigate_resp:
            log(f"cdp_navigate: Page.navigate to {url} got {navigate_resp}")
    finally:
        ws.close()


def read_display_settings() -> dict:
    if not REVELATION_DISPLAY_SETTINGS_FILE.exists():
        return {}
    try:
        return json.loads(REVELATION_DISPLAY_SETTINGS_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def apply_display_settings(url: str) -> str:
    """Forces this follower's own device-global variant/language choice
    (Settings > Revelation Peering) onto an incoming open-presentation URL,
    overwriting whatever ?variant=/?lang= Revelation's master already put
    there — a follower dedicated to, say, a lower-thirds display should
    show that regardless of what the presenter's own client is showing.
    A None/absent setting leaves that param untouched."""
    settings = read_display_settings()
    variant, lang = settings.get("variant"), settings.get("lang")
    if not variant and not lang:
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    if variant:
        query["variant"] = variant
    if lang:
        query["lang"] = lang
    return urlunsplit(parts._replace(query=urlencode(query)))


def handle_peer_command(command: dict) -> None:
    command_type = command.get("type")
    if command_type == "open-presentation":
        url = (command.get("payload") or {}).get("url")
        if url:
            url = apply_display_settings(url)
            log(f"open-presentation -> {url}")
            cdp_navigate(url)
    elif command_type == "close-presentation":
        log("close-presentation -> back to kiosk")
        cdp_navigate(KIOSK_URL)
    else:
        log(f"ignoring unknown command type {command_type!r}")


def bootstrap_socket_info(host: str, port: int, own_instance_id: str, pin: str, public_key_pem: str) -> dict | None:
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
        try:
            resp = client.get(
                f"http://{host}:{port}/peer/socket-info",
                params={"instanceId": own_instance_id, "pin": pin},
            )
        except httpx.RequestError as exc:
            log(f"{host}:{port} socket-info request failed: {exc}")
            return None
    if resp.status_code != 200:
        log(f"{host}:{port} socket-info rejected (HTTP {resp.status_code})")
        return None
    info = resp.json()
    signed_payload = f"{info['token']}:{info['expiresAt']}:{info['socketPath']}"
    if not verify_signature(public_key_pem, signed_payload.encode(), info["signature"]):
        log(f"{host}:{port} socket-info signature did not verify — refusing to connect")
        return None
    return info


def peer_worker(peer: dict, own_instance_id: str, discovery: _DiscoveryListener, connections: dict, stop_event: threading.Event) -> None:
    instance_id = peer["instanceId"]
    while not stop_event.is_set():
        host, port = discovery.get(instance_id) or (peer["hostHint"], peer["pairingPortHint"])

        info = bootstrap_socket_info(host, port, own_instance_id, peer["pairingPin"], peer["publicKey"])
        if info is None:
            connections[instance_id] = {"connected": False, "lastSeen": None}
            stop_event.wait(RECONNECT_BACKOFF_SECONDS)
            continue

        client = socketio.Client(reconnection=False)

        @client.event
        def connect():
            connections[instance_id] = {"connected": True, "lastSeen": time.time()}
            log(f"{peer['name']} ({instance_id}) connected")

        @client.event
        def disconnect():
            connections[instance_id] = {"connected": False, "lastSeen": time.time()}

        @client.on("peer-command")
        def on_peer_command(data):
            connections[instance_id]["lastCommand"] = data
            handle_peer_command(data)

        try:
            client.connect(
                info["socketUrl"],
                socketio_path=info["socketPath"].lstrip("/"),
                auth={
                    "token": info["token"],
                    "expiresAt": info["expiresAt"],
                    "signature": info["signature"],
                    "instanceId": own_instance_id,
                },
                wait_timeout=HTTP_TIMEOUT_SECONDS,
            )
            client.wait()  # blocks until disconnected
        except Exception as exc:  # noqa: BLE001 - any connect/transport failure just triggers a retry
            log(f"{peer['name']} ({instance_id}) connection error: {exc}")

        connections[instance_id] = {"connected": False, "lastSeen": time.time()}
        stop_event.wait(RECONNECT_BACKOFF_SECONDS)


def main() -> None:
    own_instance_id = get_own_instance_id()
    log(f"starting, own instanceId={own_instance_id}")

    zc = Zeroconf()
    discovery = _DiscoveryListener()
    ServiceBrowser(zc, MDNS_SERVICE_TYPE, discovery)

    connections: dict = {}
    workers: dict[str, tuple[threading.Thread, threading.Event]] = {}

    try:
        while True:
            peers = read_peers()
            paired_ids = {peer["instanceId"] for peer in peers}

            for instance_id in list(workers):
                if instance_id not in paired_ids:
                    _, stop_event = workers.pop(instance_id)
                    stop_event.set()
                    connections.pop(instance_id, None)

            for peer in peers:
                instance_id = peer["instanceId"]
                thread, stop_event = workers.get(instance_id, (None, None))
                if thread is not None and thread.is_alive():
                    continue
                stop_event = threading.Event()
                thread = threading.Thread(
                    target=peer_worker,
                    args=(peer, own_instance_id, discovery, connections, stop_event),
                    daemon=True,
                )
                workers[instance_id] = (thread, stop_event)
                thread.start()

            write_status(connections)
            time.sleep(TRUST_POLL_SECONDS)
    finally:
        zc.close()


if __name__ == "__main__":
    main()
