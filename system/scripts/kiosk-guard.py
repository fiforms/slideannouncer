#!/opt/slide-announcer/venv/bin/python
"""Kiosk guard: keeps a way back to local Settings on screen, whatever page the
kiosk's Chromium is showing (docs/KIOSK_GUARD.md).

Complements escape-hatch.py (a key combination). That one needs a keyboard and
someone who knows the keys; this one needs nobody:

  1. Floating button. On every page that is not the local app — a remote portal,
     CUPS on :631 — it injects a small "Settings" button (over the Chrome DevTools
     Protocol, loopback only; Page.addScriptToEvaluateOnNewDocument, so it
     survives navigations). Clicking it opens LANDING_URL.
  2. Network state on that button. Every cycle it checks the page's server is
     reachable (a TCP connect to the page's own host:port) and tells the button.
     When it isn't, the button turns amber and says so — the user is *told*, and
     chooses. A page may hold unsent data or run a service worker that is
     queueing it until the network returns, so a healthy page is NEVER navigated
     away from just because the network is down.
  3. Rescue. Only a page that is already lost is navigated away: a browser error
     page (chrome-error://) or a page that stops answering the debug protocol,
     for RESCUE_AFTER_SECONDS straight. Then it goes to LANDING_URL, with a
     cooldown so a down local app can't make it loop. If even that fails it
     restarts the kiosk unit, leaving the escape hatch's stay-on-Settings flag.

`Guard` is pure and unit-tested (local-app/backend/test_kiosk_guard.py); `Cdp`
and `run()` are the thin I/O. Runs unprivileged, like escape-hatch.py, under the
image's venv python (websocket-client).
"""
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import websocket

CDP_PORT = 9222
LANDING_URL = "http://localhost/settings"
KIOSK_UNIT = "slide-announcer-kiosk.service"
HOLD_FLAG = Path("/run/slide-announcer/escape-hatch-hold")

CYCLE_SECONDS = 2.0
CDP_TIMEOUT_SECONDS = 4.0
PROBE_TIMEOUT_SECONDS = 3.0
PROBE_EVERY_SECONDS = 5.0
OFFLINE_AFTER_FAILURES = 2  # consecutive failed probes before the button turns amber
UNRESPONSIVE_AFTER_FAILURES = 3  # consecutive checks the page didn't answer
RESCUE_AFTER_SECONDS = 15.0
RESCUE_COOLDOWN_SECONDS = 60.0

# Runs in every top-level page that isn't the local app. Idempotent. Shadow DOM so
# the page's CSS can't restyle it. window.__riGuard.set('ok'|'offline') is how the
# daemon updates it; the browser's own offline event also turns it amber.
BUTTON_SCRIPT = r"""
(() => {
  if (window.top !== window || window.__riGuard) return;
  const h = location.hostname;
  if ((h === 'localhost' || h === '127.0.0.1') && (location.port === '' || location.port === '80')) return;
  let daemon = 'ok';
  let host, label, btn;
  const labelFor = () => {
    const off = daemon === 'offline' || navigator.onLine === false;
    btn.dataset.state = off ? 'offline' : 'ok';
    label.textContent = off ? 'No connection — Settings' : 'Settings';
  };
  const mount = () => {
    if (host && host.isConnected) return;
    const root = document.documentElement || document.body;
    if (!root) return;
    host = document.createElement('div');
    host.style.cssText = 'all:initial;position:fixed;right:12px;bottom:12px;z-index:2147483647;';
    const shadow = host.attachShadow({mode: 'open'});
    shadow.innerHTML = '<style>button{font:600 15px system-ui,sans-serif;padding:10px 16px;border-radius:999px;' +
      'border:2px solid #fff;color:#fff;background:#1f3b63;opacity:.88;box-shadow:0 2px 8px #0008;cursor:pointer}' +
      'button:hover,button:focus{opacity:1}button[data-state=offline]{background:#b45309;opacity:1}</style>' +
      '<button type="button"><span>⌂</span> <span id="l"></span></button>';
    btn = shadow.querySelector('button');
    label = shadow.getElementById('l');
    btn.addEventListener('click', () => { location.href = %LANDING%; });
    root.appendChild(host);
    labelFor();
  };
  window.__riGuard = { set(state) { daemon = state; mount(); if (btn) labelFor(); } };
  window.addEventListener('offline', () => { mount(); if (btn) labelFor(); });
  window.addEventListener('online', () => { if (btn) labelFor(); });
  mount();
  document.addEventListener('DOMContentLoaded', mount);
  setInterval(mount, 3000); // a page that rewrites <body>/<html> can drop it
})();
""".replace("%LANDING%", json.dumps(LANDING_URL))


def log(message: str) -> None:
    print(f"kiosk-guard: {message}", flush=True)


def is_local_app(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and parts.hostname in ("localhost", "127.0.0.1") and parts.port in (None, 80, 443)


def is_web_page(url: str) -> bool:
    return urlsplit(url).scheme in ("http", "https")


def is_error_page(url: str) -> bool:
    return url.startswith("chrome-error://")


class Guard:
    """Decides when a page is lost. Feed it one observation per cycle."""

    def __init__(self, rescue_after=RESCUE_AFTER_SECONDS, cooldown=RESCUE_COOLDOWN_SECONDS,
                 unresponsive_after=UNRESPONSIVE_AFTER_FAILURES):
        self.rescue_after = rescue_after
        self.cooldown = cooldown
        self.unresponsive_after = unresponsive_after
        self._silent = 0
        self._bad_since = None
        self._last_rescue = None

    def observe(self, now: float, url: str, answered: bool) -> bool:
        """True when the page should be taken back to Settings now."""
        self._silent = 0 if answered else self._silent + 1
        lost = is_error_page(url) or self._silent >= self.unresponsive_after
        if not lost:
            self._bad_since = None
            return False
        if self._bad_since is None:
            self._bad_since = now
        if now - self._bad_since < self.rescue_after:
            return False
        if self._last_rescue is not None and now - self._last_rescue < self.cooldown:
            return False
        self._last_rescue = now
        self._bad_since = None
        self._silent = 0
        return True


class Reachability:
    """Is a page's server reachable? Debounced, and probes each origin at most every PROBE_EVERY_SECONDS."""

    def __init__(self, connect=socket.create_connection, failures=OFFLINE_AFTER_FAILURES, every=PROBE_EVERY_SECONDS):
        self._connect, self._failures, self._every = connect, failures, every
        self._state = {}  # (host, port) -> [consecutive_failures, checked_at]

    def offline(self, now: float, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        key = (parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
        fails, checked = self._state.get(key, [0, None])
        if checked is None or now - checked >= self._every:
            try:
                self._connect(key, timeout=PROBE_TIMEOUT_SECONDS).close()
                fails = 0
            except OSError:
                fails += 1
            self._state[key] = [fails, now]
        return fails >= self._failures


class CdpError(Exception):
    pass


class Cdp:
    """One connection to the browser's DevTools socket, with flattened page sessions."""

    def __init__(self, port=CDP_PORT, connect=websocket.create_connection, get_json=None):
        self._get_json = get_json or self._http_json
        self.port = port
        version = self._get_json(f"http://127.0.0.1:{port}/json/version")
        try:
            self._ws = connect(version["webSocketDebuggerUrl"], timeout=CDP_TIMEOUT_SECONDS)
        except (websocket.WebSocketException, OSError, KeyError) as exc:
            raise CdpError(f"can't connect to the browser ({exc})") from exc
        self._next_id = 0

    @staticmethod
    def _http_json(url):
        try:
            with urllib.request.urlopen(url, timeout=CDP_TIMEOUT_SECONDS) as response:
                return json.load(response)
        except (OSError, ValueError) as exc:
            raise CdpError(f"can't reach the browser's debug port ({exc})") from exc

    def pages(self):
        return [t for t in self._get_json(f"http://127.0.0.1:{self.port}/json") if t.get("type") == "page"]

    def call(self, method, params=None, session=None, timeout=CDP_TIMEOUT_SECONDS):
        self._next_id += 1
        request = {"id": self._next_id, "method": method, "params": params or {}}
        if session:
            request["sessionId"] = session
        deadline = time.monotonic() + timeout
        try:
            self._ws.send(json.dumps(request))
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise CdpError("no answer")
                self._ws.settimeout(left)
                message = json.loads(self._ws.recv())
                if message.get("id") == request["id"]:  # events, and late replies to timed-out calls, are skipped
                    if "error" in message:
                        raise CdpError(f"refused: {message['error']}")
                    return message.get("result", {})
        except (websocket.WebSocketException, OSError, ValueError) as exc:
            raise CdpError(f"no answer ({exc})") from exc

    def close(self):
        try:
            self._ws.close()
        except (websocket.WebSocketException, OSError):
            pass


def cycle(cdp: Cdp, guard: Guard, reach: Reachability, sessions: dict, now: float, restart=lambda: restart_kiosk()) -> str | None:
    """One pass over the kiosk's tab. Returns "navigated" (or "restarted") when it rescued the page."""
    pages = cdp.pages()
    for gone in set(sessions) - {p["id"] for p in pages}:
        del sessions[gone]
    if not pages:
        return None
    page = pages[0]
    url = page.get("url", "")

    session = sessions.get(page["id"])
    if session is None:
        session = cdp.call("Target.attachToTarget", {"targetId": page["id"], "flatten": True}).get("sessionId")
        if not session:
            raise CdpError("couldn't attach to the page")
        sessions[page["id"]] = session
        cdp.call("Page.enable", session=session)
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": BUTTON_SCRIPT}, session=session)

    answered = True
    if is_error_page(url):
        pass  # nothing to inject into, nothing to ask: Guard times it
    else:
        try:
            if is_web_page(url) and not is_local_app(url):
                state = "offline" if reach.offline(now, url) else "ok"
                pushed = cdp.call("Runtime.evaluate", {
                    "expression": f"window.__riGuard ? (window.__riGuard.set({json.dumps(state)}), true) : false",
                    "returnByValue": True}, session=session)
                if not pushed.get("result", {}).get("value"):  # not injected yet (page loaded before we attached)
                    cdp.call("Runtime.evaluate", {"expression": BUTTON_SCRIPT}, session=session)
                    cdp.call("Runtime.evaluate", {"expression": f"window.__riGuard&&window.__riGuard.set({json.dumps(state)})"}, session=session)
            else:
                cdp.call("Runtime.evaluate", {"expression": "1"}, session=session)
        except CdpError as exc:
            if "no answer" not in str(exc):
                raise
            answered = False

    if guard.observe(now, url, answered):
        log(f"page is lost ({url or 'no url'}); returning to Settings")
        try:
            cdp.call("Page.navigate", {"url": LANDING_URL}, session=session)
        except CdpError as exc:
            log(f"couldn't redirect the browser ({exc}); restarting the kiosk")
            restart()
            return "restarted"
        return "navigated"
    return None


def restart_kiosk(run=subprocess.run, hold_flag: Path = HOLD_FLAG) -> None:
    try:
        hold_flag.write_text(str(int(time.time())))
    except OSError as exc:
        log(f"couldn't leave the stay-on-Settings flag: {exc}")
    try:
        run(["systemctl", "restart", KIOSK_UNIT], timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"couldn't restart the kiosk: {exc}")


def main() -> int:
    guard, reach = Guard(), Reachability()
    log("watching the kiosk browser")
    while True:
        cdp, sessions = None, {}
        try:
            cdp = Cdp()
            while True:
                cycle(cdp, guard, reach, sessions, time.monotonic())
                time.sleep(CYCLE_SECONDS)
        except CdpError as exc:
            log(f"browser not available ({exc})")
        finally:
            if cdp:
                cdp.close()
        time.sleep(3.0)


if __name__ == "__main__":
    sys.exit(main())
