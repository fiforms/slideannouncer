"""Captive-portal detection and the "sign in to this network" round trip.

Detection: NetworkManager's own connectivity check is never configured on
this image (no [connectivity] uri in NetworkManager.conf), and with no URI
NM reports "full" for any connection with a default route — so a hotel/
guest network's sign-in page looked "Online". probe() does the check
itself instead: a plain-HTTP request to a URL whose real answer is a bare
204. A captive portal intercepts plain HTTP, so anything else — a redirect
(its Location is the portal page) or a 200 with the portal's own HTML —
means "portal"; no answer at all means no internet.

Some portals drop everything (DNS included) until sign-in, so the probe
gets no answer at all and looks like plain "no internet". start_sign_in()
therefore falls back to the router's own address — where the great majority
of portals are served — and the UI offers sign-in for "limited" networks
too, not just detected portals.

Sign-in: the kiosk is a single Chromium tab, so start_sign_in() hands the
frontend the portal URL to navigate that tab to, and starts a background
watcher that re-probes every few seconds. Once the probe comes back clean
(or WATCH_TIMEOUT_SECONDS passes), it sends the tab back to Settings >
Network over the Chrome DevTools Protocol — the same loopback debug port
and flattened-session approach revelation-peer-daemon.py's cdp_navigate()
uses (see that function for why a page target's own socket can't be used).
That code lives in an OS-image script this app can't import, so the
small CDP client here is a deliberate copy. If the person gets back on
their own first (the remote's Back key is plain browser history there),
the watcher sees the tab is home again and just stops.
"""
import asyncio
import json
import time
from urllib.parse import urljoin

import httpx
import websocket

PROBE_URL = "http://connectivitycheck.gstatic.com/generate_204"
PROBE_TIMEOUT_SECONDS = 5.0
CDP_PORT = 9222
KIOSK_ORIGIN = "http://localhost"
DEFAULT_RETURN_PATH = "/settings/network"
WATCH_INTERVAL_SECONDS = 3.0
WATCH_TIMEOUT_SECONDS = 600.0

_watch_task: asyncio.Task | None = None


async def probe(attempts: int = 2) -> dict:
    """{"state": "full" | "portal" | "none", "portal_url": str | None,
    "http_status": int | None, "error": str | None, "elapsed_ms": int}.
    A transport failure is retried once — right after joining a network,
    DNS commonly isn't answering for the first second or so."""
    started = time.monotonic()

    def result(state, portal_url=None, status=None, error=None):
        return {"state": state, "portal_url": portal_url, "http_status": status,
                "error": error, "elapsed_ms": round((time.monotonic() - started) * 1000)}

    error = None
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=PROBE_TIMEOUT_SECONDS) as client:
                resp = await client.get(PROBE_URL)
        except httpx.HTTPError as exc:
            error = type(exc).__name__
            if attempt + 1 < attempts:
                await asyncio.sleep(1)
                continue
            return result("none", error=error)
        if resp.status_code == 204 and not resp.content:
            return result("full", status=resp.status_code)
        location = resp.headers.get("location")
        # No redirect means the portal served its page in place of the
        # probe's own response — loading the probe URL in a browser shows it.
        return result("portal", urljoin(PROBE_URL, location) if location else PROBE_URL, resp.status_code)
    return result("none", error=error)


def _kiosk_page() -> dict | None:
    with httpx.Client(timeout=5) as client:
        targets = client.get(f"http://127.0.0.1:{CDP_PORT}/json").json()
    pages = [t for t in targets if t.get("type") == "page"]
    return pages[0] if pages else None


def _kiosk_url() -> str | None:
    page = _kiosk_page()
    return page.get("url") if page else None


def _cdp_navigate(url: str) -> None:
    with httpx.Client(timeout=5) as client:
        browser_ws = client.get(f"http://127.0.0.1:{CDP_PORT}/json/version").json()["webSocketDebuggerUrl"]
    page = _kiosk_page()
    if not page:
        return
    ws = websocket.create_connection(browser_ws, timeout=5)
    try:
        ws.send(json.dumps({"id": 1, "method": "Target.attachToTarget", "params": {"targetId": page["id"], "flatten": True}}))
        session_id = None
        for _ in range(10):
            message = json.loads(ws.recv())
            if message.get("id") == 1:
                session_id = message.get("result", {}).get("sessionId")
                break
        if not session_id:
            return
        ws.send(json.dumps({"id": 2, "method": "Page.navigate", "params": {"url": url}, "sessionId": session_id}))
        for _ in range(10):
            if json.loads(ws.recv()).get("id") == 2:
                break
    finally:
        ws.close()


async def _watch(return_url: str) -> None:
    deadline = time.monotonic() + WATCH_TIMEOUT_SECONDS
    left_home = False
    while True:
        await asyncio.sleep(WATCH_INTERVAL_SECONDS)
        try:
            url = await asyncio.to_thread(_kiosk_url)
        except Exception:  # noqa: BLE001 - kiosk restarting / debug port briefly down; keep watching
            url = None
        at_home = bool(url) and url.startswith(KIOSK_ORIGIN)
        if url and not at_home:
            left_home = True
        elif at_home and left_home:
            return  # came back on their own (Back key) — nothing to do

        timed_out = time.monotonic() >= deadline
        if not timed_out and (await probe(attempts=1))["state"] != "full":
            continue
        if not at_home:
            try:
                await asyncio.to_thread(_cdp_navigate, f"{return_url}?portal={'timeout' if timed_out else 'done'}")
            except Exception as exc:  # noqa: BLE001 - nothing more to do; Back key still works
                print(f"[captive-portal] couldn't return kiosk to settings: {exc}", flush=True)
        return


async def start_sign_in(return_path: str | None = None, gateway: str | None = None) -> str:
    """Portal URL for the kiosk tab to open, with the return watcher
    (re)started. Preference: the portal's own redirect target; else, when
    the probe got no answer at all (portal blocking DNS/HTTP), the router's
    address; else the probe URL itself, which any portal still
    intercepting traffic will answer with its sign-in page.
    `return_path` is the kiosk page to come back to — only a local path,
    so this can't be pointed off-device."""
    global _watch_task
    if not return_path or not return_path.startswith("/") or return_path.startswith("//"):
        return_path = DEFAULT_RETURN_PATH
    result = await probe()
    if _watch_task and not _watch_task.done():
        _watch_task.cancel()
    _watch_task = asyncio.create_task(_watch(f"{KIOSK_ORIGIN}{return_path}"))
    if result["portal_url"]:
        return result["portal_url"]
    if result["state"] == "none" and gateway:
        return f"http://{gateway}/"
    return PROBE_URL
