"""Live "can this device reach its AnnouncementSlides server right now?"
check for Settings > Network's Remote Slide Server row — next to the
Internet row, it separates an internet outage from a server outage.

Hits the server's Laravel health route (`/up`, see the main app's
bootstrap/app.php): unauthenticated, cheap, and a 200 only when the app
itself actually booted, not just when something answers on the port.
heartbeat/sync's last_error says whether the *last* scheduled request
failed; this answers for right now.
"""
import time
from urllib.parse import urlsplit

import httpx

import pairing

TIMEOUT_SECONDS = 6.0


async def check() -> dict:
    """{"state": "ok" | "error" | "unreachable" | "unconfigured",
        "host": str | None, "detail": str | None, "latency_ms": int | None}"""
    try:
        server_url = pairing.read_server_url()
    except pairing.PairingError as exc:
        return {"state": "unconfigured", "host": None, "detail": str(exc), "latency_ms": None}

    host = urlsplit(server_url).hostname or server_url
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
            resp = await client.get(f"{server_url}/up")
    except httpx.TimeoutException:
        return {"state": "unreachable", "host": host, "detail": "timed out", "latency_ms": None}
    except httpx.HTTPError as exc:
        # DNS failure, connection refused, or a TLS failure — the last is
        # what a captive portal intercepting HTTPS typically looks like.
        return {"state": "unreachable", "host": host, "detail": type(exc).__name__, "latency_ms": None}

    latency_ms = round((time.monotonic() - started) * 1000)
    if resp.is_success:
        return {"state": "ok", "host": host, "detail": None, "latency_ms": latency_ms}
    return {"state": "error", "host": host, "detail": f"HTTP {resp.status_code}", "latency_ms": latency_ms}
