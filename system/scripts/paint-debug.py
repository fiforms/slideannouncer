#!/opt/slide-announcer/venv/bin/python3
"""Diagnose on-screen flicker in the kiosk — is Chromium actually
repainting, or is the flicker happening somewhere downstream of it
(compositor, KMS/HDMI, or the TV's own picture processing)?

Run over SSH while looking at the TV:

    slide-announcer-paint-debug            # until Ctrl-C
    slide-announcer-paint-debug --no-overlay   # terminal stats only

While it runs, on the TV:
  - Paint flashing: every region Chromium repaints flashes green. A
    flickering border that does NOT flash green isn't being repainted by
    the page at all — look at the display path (output mode, TV settings).
  - FPS meter (top corner): a still settings page should show frames
    dropping to ~0/idle. Steady frame production on a still page means
    something keeps invalidating it.

And in this terminal, once a second: frames-worth of style recalcs and
layouts, DOM mutations (with the busiest targets), and focus changes —
so a JS-driven loop (a timer re-rendering, focus bouncing between two
controls) shows up as nonzero counts on an otherwise idle page.

Uses the same loopback CDP port and flattened Target.attachToTarget
session revelation-peer-daemon.py does (see its cdp_navigate() for why a
page target's own socket can't be used directly). The overlays belong to
this CDP session, so they disappear on their own as soon as this exits —
nothing persists on the device.
"""

import argparse
import glob
import json
import os
import pwd
import shutil
import subprocess
import sys
import time

import httpx
import websocket

CDP_PORT = 9222

# Injected into the page: counts DOM mutations and focus changes, keyed by
# a short description of the element so the busiest ones can be named.
INSTALL_PROBE = r"""
(() => {
  if (window.__paintDebug) return 'already installed'
  const describe = (node) => {
    const el = node.nodeType === 1 ? node : node.parentElement
    if (!el) return '#text'
    const cls = typeof el.className === 'string' && el.className.trim()
      ? '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.') : ''
    return el.tagName.toLowerCase() + cls
  }
  const state = { mutations: 0, focus: 0, targets: {}, focusTargets: {} }
  const observer = new MutationObserver((records) => {
    for (const r of records) {
      state.mutations++
      const key = describe(r.target) + ' ' + (r.type === 'attributes' ? '@' + r.attributeName : r.type)
      state.targets[key] = (state.targets[key] || 0) + 1
    }
  })
  observer.observe(document, { subtree: true, childList: true, attributes: true, characterData: true })
  const onFocus = (e) => {
    state.focus++
    const key = describe(e.target)
    state.focusTargets[key] = (state.focusTargets[key] || 0) + 1
  }
  document.addEventListener('focusin', onFocus, true)
  window.__paintDebug = {
    take() {
      const top = (o) => Object.entries(o).sort((a, b) => b[1] - a[1]).slice(0, 4)
      const out = {
        mutations: state.mutations, focus: state.focus,
        targets: top(state.targets), focusTargets: top(state.focusTargets),
        path: location.pathname,
      }
      state.mutations = 0; state.focus = 0; state.targets = {}; state.focusTargets = {}
      return JSON.stringify(out)
    },
    remove() {
      observer.disconnect()
      document.removeEventListener('focusin', onFocus, true)
      delete window.__paintDebug
    },
  }
  return 'installed'
})()
"""


class Session:
    def __init__(self):
        with httpx.Client(timeout=5) as client:
            browser_info = client.get(f"http://127.0.0.1:{CDP_PORT}/json/version").json()
            targets = client.get(f"http://127.0.0.1:{CDP_PORT}/json").json()
        pages = [t for t in targets if t.get("type") == "page"]
        if not pages:
            sys.exit("no page target on the kiosk's debug port — is the kiosk running?")
        self.ws = websocket.create_connection(browser_info["webSocketDebuggerUrl"], timeout=5)
        self.next_id = 0
        attached = self.send("Target.attachToTarget", {"targetId": pages[0]["id"], "flatten": True}, session=False)
        self.session_id = attached["sessionId"]
        print(f"attached to {pages[0].get('url')}")

    def send(self, method, params=None, session=True):
        self.next_id += 1
        message = {"id": self.next_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = self.session_id
        self.ws.send(json.dumps(message))
        # Skip interleaved events until our response arrives.
        while True:
            reply = json.loads(self.ws.recv())
            if reply.get("id") == self.next_id:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error']}")
                return reply.get("result", {})

    def evaluate(self, expression):
        result = self.send("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return result.get("result", {}).get("value")


def print_output_mode():
    """The HDMI output's current mode, two ways: wlr-randr's view (what the
    compositor asked for) and the kernel's DRM state (what's actually being
    sent, including the interlace flag wlr-randr doesn't show)."""
    wlr_randr = shutil.which("wlr-randr")
    if not wlr_randr:
        print("  wlr-randr: NOT INSTALLED — apply-screen-resolution.sh can't set the mode either")
    else:
        try:
            uid = pwd.getpwnam("slideannouncer").pw_uid
        except KeyError:
            uid = os.getuid()
        # Root can reach the kiosk user's Wayland socket directly.
        env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{uid}", "WAYLAND_DISPLAY": "wayland-1"}
        try:
            out = subprocess.run([wlr_randr], env=env, capture_output=True, text=True, timeout=5)
            if out.returncode != 0:
                print(f"  wlr-randr failed: {out.stderr.strip() or out.returncode} (run as root)")
            for line in out.stdout.splitlines():
                stripped = line.strip()
                if not line.startswith(" ") or "current" in stripped or stripped.startswith(("Enabled", "Scale", "Transform", "Adaptive")):
                    print("  " + stripped)
        except (OSError, subprocess.TimeoutExpired) as err:
            print(f"  wlr-randr unavailable: {err}")

    # debugfs DRM atomic state: each active CRTC's line looks like
    #   mode: "1920x1080": 60 148500 1920 2008 2052 2200 1080 1084 1089 1125 0x48 0x5
    # (refresh, pixel clock kHz, timings, type, flags — flags bit 0x10 is
    # DRM_MODE_FLAG_INTERLACE).
    states = glob.glob("/sys/kernel/debug/dri/*/state")
    if not states:
        print("  DRM state: unavailable (needs root + debugfs)")
    for path in states:
        try:
            text = open(path).read()
        except OSError as err:
            print(f"  DRM state {path}: {err}")
            continue
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("mode:") and '""' not in line:
                parts = line.split()
                flags = int(parts[-1], 16) if parts[-1].startswith("0x") else 0
                interlaced = " INTERLACED" if flags & 0x10 else ""
                print(f"  DRM {path.split('/')[-2]}: {line}{interlaced}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-overlay", action="store_true", help="don't show paint flashing / FPS meter on the TV")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between stat lines (default 1)")
    args = parser.parse_args()

    print("HDMI output (wlr-randr):")
    print_output_mode()

    s = Session()
    s.send("Runtime.enable")
    s.send("Performance.enable")
    if not args.no_overlay:
        s.send("DOM.enable")
        s.send("Overlay.enable")
        s.send("Overlay.setShowPaintRects", {"result": True})
        s.send("Overlay.setShowFPSCounter", {"show": True})
        print("paint flashing + FPS meter ON (green = Chromium repainted that region)")
    print(s.evaluate(INSTALL_PROBE))
    print("Ctrl-C to stop.\n")

    def metrics():
        return {m["name"]: m["value"] for m in s.send("Performance.getMetrics")["metrics"]}

    prev = metrics()
    try:
        while True:
            time.sleep(args.interval)
            cur = metrics()
            raw = s.evaluate("window.__paintDebug && window.__paintDebug.take()")
            if raw is None:
                # Full page load (e.g. Revelation took over) wiped the probe.
                print(s.evaluate(INSTALL_PROBE))
                prev = cur
                continue
            stats = json.loads(raw)
            styles = int(cur["RecalcStyleCount"] - prev["RecalcStyleCount"])
            layouts = int(cur["LayoutCount"] - prev["LayoutCount"])
            script_ms = (cur["ScriptDuration"] - prev["ScriptDuration"]) * 1000
            prev = cur
            line = (f"{time.strftime('%H:%M:%S')} {stats['path']:<28} "
                    f"style={styles:<3} layout={layouts:<3} script={script_ms:5.1f}ms "
                    f"mutations={stats['mutations']:<4} focus={stats['focus']}")
            print(line)
            for key, count in stats["targets"]:
                print(f"    mut  {count:>4}  {key}")
            for key, count in stats["focusTargets"]:
                print(f"    foc  {count:>4}  {key}")
    except KeyboardInterrupt:
        pass
    finally:
        try:
            s.evaluate("window.__paintDebug && window.__paintDebug.remove()")
        except Exception:
            pass
        s.ws.close()
        print("\nstopped — overlays removed.")


if __name__ == "__main__":
    main()
