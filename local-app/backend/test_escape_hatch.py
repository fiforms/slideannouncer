"""The escape hatch (docs/ESCAPE_HATCH.md): combo detection, what it does when
triggered, the kiosk-navigate helper's CDP conversation, and the stay-on-Settings
flag. The two scripts are loaded from system/scripts, since they are standalone
OS-image scripts rather than backend modules."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import time

import pytest

import escape_hatch
import main

ROOT = os.environ.get("PLATFORM_ROOT")
pytestmark = pytest.mark.skipif(not ROOT, reason="PLATFORM_ROOT not set (run through local-app/run-tests.sh)")


def load_script(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), f"{ROOT}/system/scripts/{name}.py")
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


hatch = load_script("escape-hatch") if ROOT else None
navigate = load_script("kiosk-navigate") if ROOT else None


# ---- combo detection

KB = "/dev/input/event0"
REMOTE = "/dev/input/event1"


def press(d, source, *names, t=0.0):
    out = []
    for name in names:
        out += d.key(source, name, 1, t)
    return out


def names(fired):
    return [c.name for c in fired]


def test_ctrl_alt_home_fires_when_the_last_key_goes_down_whichever_order():
    d = hatch.Detector()
    assert press(d, KB, "KEY_LEFTCTRL", "KEY_LEFTALT") == []
    assert names(press(d, KB, "KEY_HOME")) == ["Ctrl+Alt+Home"]

    d = hatch.Detector()
    assert names(press(d, KB, "KEY_HOME", "KEY_RIGHTALT", "KEY_RIGHTCTRL")) == ["Ctrl+Alt+Home"]


def test_ctrl_alt_s_works_without_a_home_key():
    d = hatch.Detector()
    assert names(press(d, KB, "KEY_LEFTCTRL", "KEY_LEFTALT", "KEY_S")) == ["Ctrl+Alt+S"]


@pytest.mark.parametrize("keys", [
    ("KEY_LEFTCTRL", "KEY_HOME"), ("KEY_LEFTALT", "KEY_HOME"), ("KEY_HOME",), ("KEY_S",),
    ("KEY_LEFTCTRL", "KEY_LEFTALT", "KEY_A"), ("KEY_LEFTSHIFT", "KEY_LEFTALT", "KEY_S"),
])
def test_partial_or_wrong_combinations_do_nothing(keys):
    assert press(hatch.Detector(), KB, *keys) == []


def test_released_keys_no_longer_count():
    d = hatch.Detector()
    press(d, KB, "KEY_LEFTCTRL", "KEY_LEFTALT")
    d.key(KB, "KEY_LEFTCTRL", 0, 0.1)
    assert press(d, KB, "KEY_HOME", t=0.2) == []


def test_autorepeat_never_fires_or_re_fires():
    d = hatch.Detector(cooldown=0)
    press(d, KB, "KEY_LEFTCTRL", "KEY_LEFTALT", "KEY_HOME")
    assert d.key(KB, "KEY_HOME", 2, 0.5) == []


def test_modifiers_and_key_may_come_from_different_keyboards():
    d = hatch.Detector()
    press(d, KB, "KEY_LEFTCTRL", "KEY_LEFTALT")
    assert names(press(d, "/dev/input/event5", "KEY_HOME")) == ["Ctrl+Alt+Home"]


def test_a_second_trigger_inside_the_cooldown_is_ignored_then_allowed():
    d = hatch.Detector(cooldown=3)
    assert press(d, KB, "KEY_LEFTCTRL", "KEY_LEFTALT", "KEY_HOME", t=10)
    d.key(KB, "KEY_HOME", 0, 10.1)
    assert press(d, KB, "KEY_HOME", t=11) == []
    d.key(KB, "KEY_HOME", 0, 11.1)
    assert names(press(d, KB, "KEY_HOME", t=14)) == ["Ctrl+Alt+Home"]


def test_remote_home_fires_only_after_being_held_long_enough():
    d = hatch.Detector()
    assert press(d, REMOTE, "KEY_HOMEPAGE", t=0) == []
    assert d.wait(0.0) == pytest.approx(3.0)
    assert d.poll(2.9) == []
    assert names(d.poll(3.0)) == ["hold Home (remote)"]
    assert d.poll(10.0) == [] and d.wait(10.0) is None  # once per hold


def test_a_tap_of_remote_home_does_nothing():
    d = hatch.Detector()
    press(d, REMOTE, "KEY_HOMEPAGE", t=0)
    d.key(REMOTE, "KEY_HOMEPAGE", 0, 0.2)
    assert d.poll(5.0) == [] and d.wait(5.0) is None


def test_unplugging_the_device_mid_hold_cancels_it():
    d = hatch.Detector()
    press(d, REMOTE, "KEY_HOMEPAGE", t=0)
    d.release_source(REMOTE)
    assert d.poll(5.0) == []


def test_a_held_home_can_be_repeated_after_release():
    d = hatch.Detector(cooldown=0)
    press(d, REMOTE, "KEY_HOMEPAGE", t=0)
    assert d.poll(3.0)
    d.key(REMOTE, "KEY_HOMEPAGE", 0, 3.1)
    press(d, REMOTE, "KEY_HOMEPAGE", t=4)
    assert d.poll(7.0)


# ---- what it does when triggered

class Runner:
    def __init__(self, navigate_rc=0, navigate_raises=None):
        self.calls, self.navigate_rc, self.navigate_raises = [], navigate_rc, navigate_raises

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[0] == hatch.NAVIGATE_CLI:
            if self.navigate_raises:
                raise self.navigate_raises
            return subprocess.CompletedProcess(argv, self.navigate_rc, "", "browser said no")
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_a_working_browser_is_just_redirected(tmp_path):
    run, flag = Runner(), tmp_path / "hold"
    assert hatch.escape(run, flag) == "navigated"
    assert run.calls == [[hatch.NAVIGATE_CLI, "http://localhost/settings"]]
    assert not flag.exists()


@pytest.mark.parametrize("runner", [Runner(navigate_rc=1), Runner(navigate_raises=subprocess.TimeoutExpired("x", 20)),
                                    Runner(navigate_raises=FileNotFoundError())])
def test_if_the_redirect_fails_it_leaves_the_flag_and_restarts_the_kiosk(tmp_path, runner):
    flag = tmp_path / "hold"
    assert hatch.escape(runner, flag) == "restarted"
    assert runner.calls[-1] == ["systemctl", "restart", "slide-announcer-kiosk.service"]
    assert flag.exists()


def test_a_missing_flag_directory_does_not_stop_the_restart(tmp_path):
    run = Runner(navigate_rc=1)
    assert hatch.escape(run, tmp_path / "no-such-dir" / "hold") == "restarted"
    assert run.calls[-1][0] == "systemctl"


# ---- the stay-on-Settings flag

@pytest.fixture
def flag(tmp_path, monkeypatch):
    monkeypatch.setattr(escape_hatch, "HOLD_FLAG", tmp_path / "hold")
    return tmp_path / "hold"


def test_the_hold_is_given_exactly_once(flag):
    assert main.consume_escape_hold() == {"held": False}
    flag.write_text("1")
    assert main.consume_escape_hold() == {"held": True}
    assert main.consume_escape_hold() == {"held": False}


def test_a_stale_hold_is_cleared_but_not_honoured(flag):
    flag.write_text("1")
    old = time.time() - escape_hatch.MAX_AGE_SECONDS - 10
    os.utime(flag, (old, old))
    assert escape_hatch.consume_hold() is False
    assert not flag.exists()


# ---- kiosk-navigate's DevTools conversation

class FakeSocket:
    """A browser socket: answers attach with a session, navigate per `navigate_result`,
    and sprinkles unrelated events in first, as Chromium does."""

    def __init__(self, navigate_result=None, attach_result=None, drop_after=None):
        self.sent, self._queue, self.closed = [], [], False
        self.navigate_result = navigate_result if navigate_result is not None else {"result": {"frameId": "F"}}
        self.attach_result = attach_result if attach_result is not None else {"result": {"sessionId": "S1"}}
        self.drop_after = drop_after

    def send(self, text):
        message = json.loads(text)
        self.sent.append(message)
        self._queue.append({"method": "Target.somethingElse", "params": {}})
        reply = self.attach_result if message["id"] == 1 else self.navigate_result
        self._queue.append({"id": message["id"], **reply})

    def recv(self):
        if self.drop_after is not None and len(self.sent) >= self.drop_after:
            raise OSError("connection lost")
        return json.dumps(self._queue.pop(0))

    def close(self):
        self.closed = True


def fake_http(pages):
    def get_json(url):
        return {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/x"} if url.endswith("/json/version") else pages
    return get_json


PAGES = [{"type": "service_worker", "id": "SW"}, {"type": "page", "id": "PAGE1"}, {"type": "page", "id": "PAGE2"}]


def test_navigates_the_first_page_target_through_a_flattened_session():
    ws = FakeSocket()
    navigate.navigate("http://localhost/settings", get_json=fake_http(PAGES), connect=lambda url, timeout: ws)
    attach, go = ws.sent
    assert attach["method"] == "Target.attachToTarget" and attach["params"] == {"targetId": "PAGE1", "flatten": True}
    assert go["method"] == "Page.navigate" and go["sessionId"] == "S1" and go["params"] == {"url": "http://localhost/settings"}
    assert ws.closed


@pytest.mark.parametrize("pages, ws, message", [
    ([], FakeSocket(), "no page"),
    (PAGES, FakeSocket(attach_result={"error": {"message": "nope"}}), "refused"),
    (PAGES, FakeSocket(attach_result={"result": {}}), "attach"),
    (PAGES, FakeSocket(navigate_result={"result": {"errorText": "net::ERR_BLOCKED"}}), "didn't load"),
    (PAGES, FakeSocket(drop_after=1), "no answer"),
])
def test_failures_are_reported_and_the_socket_is_closed(pages, ws, message):
    with pytest.raises(navigate.NavigateError, match=message):
        navigate.navigate("http://localhost/settings", get_json=fake_http(pages), connect=lambda url, timeout: ws)
    assert ws.closed or not ws.sent


def test_no_browser_listening_is_a_clean_error(capsys):
    def refuse(url):
        raise navigate.NavigateError("can't reach the browser's debug port")
    with pytest.raises(navigate.NavigateError):
        navigate.navigate("http://localhost/", get_json=refuse)
    assert navigate.main(["x", "ftp://nope"]) == 2


# ---- against a real Chrome (opt-in: needs a chrome/chromium binary and KIOSK_CHROME_TEST=1)

CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")


@pytest.mark.skipif(not (CHROME and os.environ.get("KIOSK_CHROME_TEST") == "1"), reason="set KIOSK_CHROME_TEST=1 with Chrome installed")
def test_really_redirects_a_running_chrome(tmp_path):
    import http.server
    import socket
    import threading
    import urllib.request

    def free_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<title>settings-page</title>ok")
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(site), **k)  # noqa: E731
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = free_port()
    chrome = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={port}", "--remote-allow-origins=*",
                               f"--user-data-dir={tmp_path / 'profile'}", "--no-sandbox", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        navigate.navigate(url, port=port)
        time.sleep(1)
        pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
        assert any(p["type"] == "page" and p["url"] == url and p["title"] == "settings-page" for p in pages)
    finally:
        chrome.terminate()
        chrome.wait(10)
        server.shutdown()
