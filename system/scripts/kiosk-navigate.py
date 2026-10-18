#!/opt/slide-announcer/venv/bin/python
"""Sends the kiosk's already-running Chromium tab to a URL, over the Chrome
DevTools Protocol (kiosk-start.sh's --remote-debugging-port, loopback only).

    slide-announcer-kiosk-navigate http://localhost/settings

Exit status 0 once Chromium accepted the navigation; 1 (with the reason on
stderr) if it could not be done — no browser listening, no page, a refusal, or
no answer in time; 2 for bad usage. Used by escape-hatch.py, which falls back
to restarting the kiosk when this fails; anything else that needs to move the
kiosk's tab (a watchdog, a product's own trigger) should call this rather than
carry another copy of the protocol.

Runs under the image's venv python (it needs websocket-client, which the
system python doesn't have), as an OS-image script independent of the
versioned local-app release.

Why it is shaped this way: a page target's own websocket never answers on
current Chromium, so connect to the *browser* socket, Target.attachToTarget
with flatten=true to get a sessionId, and tag Page.navigate with it. Older
copies of the same dance (local-app/backend/captive_portal.py's
_cdp_navigate, paint-debug.py, and a signage product's revelation-peer script)
predate this one and could move to it.
"""
import json
import sys
import urllib.request
from urllib.parse import urlsplit

import websocket

CDP_PORT = 9222
TIMEOUT_SECONDS = 8.0
MAX_MESSAGES = 50  # events interleave with replies; don't wait forever for ours


class NavigateError(Exception):
    pass


def _get_json(url: str):
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            return json.load(response)
    except OSError as exc:  # URLError, timeouts and refused connections are all OSErrors
        raise NavigateError(f"can't reach the browser's debug port ({exc})") from exc


def _reply(ws, request_id: int) -> dict:
    for _ in range(MAX_MESSAGES):
        try:
            message = json.loads(ws.recv())
        except (websocket.WebSocketException, OSError, ValueError) as exc:
            raise NavigateError(f"no answer from the browser ({exc})") from exc
        if message.get("id") == request_id:
            if "error" in message:
                raise NavigateError(f"the browser refused: {message['error']}")
            return message
    raise NavigateError("no answer from the browser")


def navigate(url: str, port: int = CDP_PORT, get_json=_get_json, connect=websocket.create_connection) -> None:
    base = f"http://127.0.0.1:{port}"
    browser_socket = get_json(f"{base}/json/version").get("webSocketDebuggerUrl")
    pages = [t for t in get_json(f"{base}/json") if t.get("type") == "page"]
    if not browser_socket or not pages:
        raise NavigateError("the browser has no page open")

    try:
        ws = connect(browser_socket, timeout=TIMEOUT_SECONDS)
    except (websocket.WebSocketException, OSError) as exc:
        raise NavigateError(f"can't connect to the browser ({exc})") from exc
    try:
        ws.send(json.dumps({"id": 1, "method": "Target.attachToTarget", "params": {"targetId": pages[0]["id"], "flatten": True}}))
        session_id = _reply(ws, 1).get("result", {}).get("sessionId")
        if not session_id:
            raise NavigateError("couldn't attach to the page")
        ws.send(json.dumps({"id": 2, "method": "Page.navigate", "params": {"url": url}, "sessionId": session_id}))
        result = _reply(ws, 2).get("result", {})
        if result.get("errorText"):
            raise NavigateError(f"the page didn't load ({result['errorText']})")
    finally:
        ws.close()


def main(argv) -> int:
    if len(argv) != 2 or urlsplit(argv[1]).scheme not in ("http", "https"):
        print("usage: slide-announcer-kiosk-navigate http(s)://URL", file=sys.stderr)
        return 2
    try:
        navigate(argv[1])
    except NavigateError as exc:
        print(f"slide-announcer-kiosk-navigate: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
