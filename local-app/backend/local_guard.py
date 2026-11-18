"""Keeps web pages the kiosk browser loads from driving the device's own API.

The kiosk does not only show our local app: Revelation peering navigates the
tab to a master's presentation, the captive-portal flow shows a hotspot's
sign-in page, and future content types may embed third-party players. Any
such page runs inside the same browser on the same machine, so loopback
binding alone does not protect /api/local/* — a plain cross-site
<form method=post action="http://localhost/api/local/system/factory-reset">
would otherwise work. This middleware closes three holes:

* Foreign Host header → refused. Defeats DNS rebinding (an attacker's name
  re-pointed at 127.0.0.1 makes the browser treat our API as same-origin
  with their page). Checked here as well as in nginx because the backend's
  own 127.0.0.1:8000 listener is reachable the same way.
* Foreign Origin header → refused. Browsers attach Origin to cross-origin
  fetches, every non-GET request and every WebSocket handshake.
* State-changing method without the X-Local-App header → refused. A
  cross-origin page cannot add a custom header without a CORS preflight,
  and this API never answers one, so only our own frontend (src/api.js adds
  it to every request) and local tools can send it. HTML forms cannot set
  headers at all.

Local non-browser callers (the updater's health check, curl on the device)
send no Origin and a 127.0.0.1/localhost Host, so they pass unchanged; a
local tool that POSTs just adds the header.
"""
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

HEADER = "x-local-app"
LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _hostname(host: str) -> str:
    """Host header value without its port, IPv6 brackets removed, lowercased."""
    host = host.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end != -1 else ""
    return host.split(":", 1)[0]


def is_local_host(host) -> bool:
    return bool(host) and _hostname(host) in LOCAL_HOSTNAMES


def is_local_origin(origin: str) -> bool:
    """An Origin header naming a loopback page (any port, so `npm run dev` works)."""
    try:
        parts = urlsplit(origin.strip())
        hostname = parts.hostname
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and hostname in LOCAL_HOSTNAMES


def rejection(scope_type: str, method, headers: dict):
    """Why this request must be refused, or None if it may proceed."""
    if not is_local_host(headers.get("host")):
        return "foreign Host"
    origin = headers.get("origin")
    if origin is not None and not is_local_origin(origin):
        return "foreign Origin"
    if scope_type == "http" and method not in SAFE_METHODS and headers.get(HEADER) != "1":
        return f"missing {HEADER} header"
    return None


class LocalGuard:
    """Pure ASGI middleware so it covers WebSocket routes as well as HTTP."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        reason = rejection(scope["type"], scope.get("method"), headers)
        if reason is None:
            return await self.app(scope, receive, send)
        if scope["type"] == "http":
            return await JSONResponse({"detail": f"Refused: {reason}"}, status_code=403)(scope, receive, send)
        # Closing before accept makes the server answer the handshake with 403.
        await send({"type": "websocket.close", "code": 1008})
