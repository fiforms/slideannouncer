"""The kiosk guard (docs/KIOSK_GUARD.md): when a page counts as lost, the
reachability debounce, the per-cycle CDP conversation, and (opt-in) the injected
button in a real Chrome. Loaded from system/scripts like test_escape_hatch.py."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import time

import pytest

ROOT = os.environ.get("PLATFORM_ROOT")
pytestmark = pytest.mark.skipif(not ROOT, reason="PLATFORM_ROOT not set (run through local-app/run-tests.sh)")


def load_script(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), f"{ROOT}/system/scripts/{name}.py")
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


guard_mod = load_script("kiosk-guard") if ROOT else None
WEB = "https://portal.example.org/app"
ERR = "chrome-error://chromewebdata/"


# ---- Guard: only a lost page is navigated

def test_a_healthy_page_is_never_rescued_however_long():
    g = guard_mod.Guard()
    assert not any(g.observe(t, WEB, True) for t in range(0, 600, 2))


def test_an_error_page_is_rescued_only_after_the_grace_period():
    g = guard_mod.Guard(rescue_after=15)
    assert not g.observe(0, ERR, True)
    assert not g.observe(14, ERR, True)
    assert g.observe(15, ERR, True)


def test_recovering_before_the_grace_period_resets_it():
    g = guard_mod.Guard(rescue_after=15)
    g.observe(0, ERR, True)
    assert not g.observe(10, WEB, True)
    assert not g.observe(20, ERR, True)  # a fresh 15 s starts here
    assert g.observe(35, ERR, True)


def test_a_page_that_stops_answering_is_rescued_after_three_silences_and_the_grace_period():
    g = guard_mod.Guard(rescue_after=15, unresponsive_after=3)
    assert not g.observe(0, WEB, False)
    assert not g.observe(2, WEB, False)
    assert not g.observe(4, WEB, False)  # lost from here
    assert not g.observe(18, WEB, False)
    assert g.observe(19, WEB, False)


def test_one_missed_answer_is_forgiven():
    g = guard_mod.Guard(rescue_after=0, unresponsive_after=3)
    assert not g.observe(0, WEB, False)
    assert not g.observe(2, WEB, True)
    assert not g.observe(4, WEB, False)
    assert not g.observe(6, WEB, False)


def test_cooldown_stops_a_down_local_app_looping():
    g = guard_mod.Guard(rescue_after=5, cooldown=60)
    g.observe(0, ERR, True)
    assert g.observe(5, ERR, True)
    g.observe(6, ERR, True)
    assert not g.observe(30, ERR, True)
    assert g.observe(66, ERR, True)


# ---- Reachability: told, debounced, rate-limited

class Connector:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def __call__(self, key, timeout):
        self.calls.append(key)
        if not self.ok:
            raise OSError("unreachable")

        class Sock:
            def close(self):
                pass
        return Sock()


def test_offline_needs_two_failed_probes_and_recovers_on_one_success():
    c = Connector(ok=False)
    r = guard_mod.Reachability(connect=c, failures=2, every=5)
    assert r.offline(0, WEB) is False
    assert r.offline(5, WEB) is True
    c.ok = True
    assert r.offline(10, WEB) is False


def test_probes_the_pages_own_host_and_port_at_most_every_interval():
    c = Connector()
    r = guard_mod.Reachability(connect=c, every=5)
    r.offline(0, "https://portal.example.org/x")
    r.offline(2, "https://portal.example.org/y")
    r.offline(6, "http://localhost:631/admin")
    assert c.calls == [("portal.example.org", 443), ("localhost", 631)]


def test_non_web_urls_are_never_probed():
    c = Connector(ok=False)
    r = guard_mod.Reachability(connect=c)
    assert r.offline(0, ERR) is False and r.offline(0, "about:blank") is False
    assert c.calls == []


def test_local_app_origin_is_recognised_but_cups_is_not():
    assert guard_mod.is_local_app("http://localhost/settings")
    assert guard_mod.is_local_app("http://127.0.0.1/")
    assert not guard_mod.is_local_app("http://localhost:631/admin")
    assert not guard_mod.is_local_app(WEB)


# ---- cycle(): the CDP conversation, against a fake browser

class FakeCdp:
    def __init__(self, url, ids=("T1",), silent=False, value=True):
        self.url, self.ids, self.silent, self.value, self.calls = url, ids, silent, value, []

    def pages(self):
        return [{"id": i, "type": "page", "url": self.url} for i in self.ids]

    def call(self, method, params=None, session=None, timeout=4):
        self.calls.append((method, params))
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        if method == "Runtime.evaluate" and self.silent:
            raise guard_mod.CdpError("no answer")
        if method == "Runtime.evaluate":
            return {"result": {"value": self.value}}
        return {}

    def methods(self):
        return [m for m, _ in self.calls]


def run_cycle(cdp, g=None, r=None, sessions=None, now=0.0, restart=lambda: None):
    return guard_mod.cycle(cdp, g or guard_mod.Guard(), r or guard_mod.Reachability(connect=Connector()), sessions if sessions is not None else {}, now, restart)


def test_first_sight_of_a_page_attaches_and_registers_the_button_script():
    cdp = FakeCdp(WEB)
    run_cycle(cdp)
    assert cdp.methods()[:3] == ["Target.attachToTarget", "Page.enable", "Page.addScriptToEvaluateOnNewDocument"]
    script = [p for m, p in cdp.calls if m == "Page.addScriptToEvaluateOnNewDocument"][0]["source"]
    assert "__riGuard" in script and "http://localhost/settings" in script


def test_the_session_is_reused_on_later_cycles():
    cdp, sessions = FakeCdp(WEB), {}
    run_cycle(cdp, sessions=sessions)
    cdp.calls.clear()
    run_cycle(cdp, sessions=sessions, now=2)
    assert "Target.attachToTarget" not in cdp.methods()


def test_a_page_loaded_before_attach_gets_the_script_evaluated_into_it():
    cdp = FakeCdp(WEB, value=False)
    run_cycle(cdp)
    evals = [p["expression"] for m, p in cdp.calls if m == "Runtime.evaluate"]
    assert evals[1] == guard_mod.BUTTON_SCRIPT


def test_network_down_tells_the_button_and_never_navigates_a_healthy_page():
    cdp = FakeCdp(WEB)
    r = guard_mod.Reachability(connect=Connector(ok=False), failures=1)
    for t in range(0, 300, 2):
        assert run_cycle(cdp, r=r, sessions={"T1": "S1"}, now=t) is None
    assert "Page.navigate" not in cdp.methods()
    assert any('set("offline")' in (p or {}).get("expression", "") for m, p in cdp.calls)


def test_the_local_app_is_only_checked_for_a_pulse_not_decorated():
    cdp = FakeCdp("http://localhost/settings")
    run_cycle(cdp)
    assert [p["expression"] for m, p in cdp.calls if m == "Runtime.evaluate"] == ["1"]


def test_an_error_page_is_navigated_to_settings_after_the_grace_period():
    cdp, g = FakeCdp(ERR), guard_mod.Guard(rescue_after=15)
    assert run_cycle(cdp, g=g, now=0) is None
    assert run_cycle(cdp, g=g, sessions={"T1": "S1"}, now=15) == "navigated"
    assert ("Page.navigate", {"url": "http://localhost/settings"}) in cdp.calls
    assert "Runtime.evaluate" not in cdp.methods()


def test_a_hung_page_is_navigated_once_it_has_been_silent_long_enough():
    cdp, g, sessions = FakeCdp(WEB, silent=True), guard_mod.Guard(rescue_after=10), {"T1": "S1"}
    results = [run_cycle(cdp, g=g, sessions=sessions, now=t) for t in range(0, 20, 2)]
    assert results.count("navigated") == 1


def test_if_the_navigation_itself_fails_the_kiosk_is_restarted():
    class Refusing(FakeCdp):
        def call(self, method, *a, **k):
            if method == "Page.navigate":
                raise guard_mod.CdpError("no answer")
            return super().call(method, *a, **k)

    restarted = []
    cdp, g = Refusing(ERR), guard_mod.Guard(rescue_after=0)
    assert run_cycle(cdp, g=g, sessions={"T1": "S1"}, restart=lambda: restarted.append(1)) == "restarted"
    assert restarted == [1]


def test_sessions_for_closed_tabs_are_forgotten():
    sessions = {"OLD": "S0"}
    run_cycle(FakeCdp(WEB), sessions=sessions)
    assert "OLD" not in sessions


# ---- the injected script

@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_button_script_is_valid_javascript(tmp_path):
    f = tmp_path / "button.js"
    f.write_text(guard_mod.BUTTON_SCRIPT)
    subprocess.run(["node", "--check", str(f)], check=True)


# ---- against a real Chrome (opt-in: KIOSK_CHROME_TEST=1)

CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")


@pytest.mark.skipif(not (CHROME and os.environ.get("KIOSK_CHROME_TEST") == "1"), reason="set KIOSK_CHROME_TEST=1 with Chrome installed")
def test_really_injects_the_button_and_turns_it_amber(tmp_path):
    import http.server
    import socket
    import threading
    import urllib.request

    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<title>remote</title><body>remote page</body>")
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(site), **k)  # noqa: E731
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    chrome = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={port}", "--remote-allow-origins=*",
                               f"--user-data-dir={tmp_path / 'profile'}", "--no-sandbox", f"http://127.0.0.1:{server.server_address[1]}/"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        cdp = guard_mod.Cdp(port=port)
        sessions, g = {}, guard_mod.Guard()
        r = guard_mod.Reachability(connect=Connector())
        guard_mod.cycle(cdp, g, r, sessions, 0.0)
        session = next(iter(sessions.values()))

        def read(expr):
            return cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True}, session=session)["result"]["value"]

        probe = "document.querySelector('div').shadowRoot.querySelector('button')"
        assert read(f"{probe}.dataset.state") == "ok"
        assert read(f"{probe}.textContent").strip().endswith("Settings")
        guard_mod.cycle(cdp, g, guard_mod.Reachability(connect=Connector(ok=False), failures=1), sessions, 2.0)
        assert read(f"{probe}.dataset.state") == "offline"
        # survives a navigation
        cdp.call("Page.navigate", {"url": f"http://127.0.0.1:{server.server_address[1]}/?again"}, session=session)
        time.sleep(1)
        guard_mod.cycle(cdp, g, r, sessions, 4.0)
        assert read(f"{probe}.dataset.state") == "ok"
        cdp.close()
    finally:
        chrome.terminate()
        chrome.wait(10)
        server.shutdown()
