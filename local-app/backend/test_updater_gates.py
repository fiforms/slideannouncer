"""When the automatic updaters may apply: the overnight window, the overdue
override, and the update hold (docs/DEVICE_CONTRACT.md, "Overdue updates" and
"Update hold"). Both updaters carry their own copy of the rules (they run
outside the backend's venv), so each is exercised against the same cases."""
import importlib.machinery
import importlib.util
import json
import os

import pytest

ROOT = os.environ.get("PLATFORM_ROOT")
pytestmark = pytest.mark.skipif(not ROOT, reason="PLATFORM_ROOT not set (run through local-app/run-tests.sh)")

OS_INFO = {
    "os_update_available": True, "latest_os_version": "2.0.0", "os_bundle_url": "https://cdn/os.raucb",
    "os_release_type": "full", "os_update_age_hours": 1, "update_overdue_after_hours": 72,
}
APP_INFO = {
    "app_update_available": True, "latest_app_version": "2.0.0", "app_download_url": "https://cdn/app.tgz",
    "app_update_age_hours": 1, "update_overdue_after_hours": 72,
}


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, f"{ROOT}/{path}")
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


@pytest.fixture(params=["os", "app"])
def updater(request, monkeypatch, tmp_path):
    """(module, heartbeat info, kind) with everything but the gates stubbed out. A held
    lock stops main() right after the gates, so "reached the lock" == "would have applied"."""
    if request.param == "os":
        module, info = load("os_updater", "system/scripts/os-updater.py"), dict(OS_INFO)
        monkeypatch.setattr(module, "VIRT_IMAGE_MARKER", tmp_path / "no-virt")
        monkeypatch.setattr(module, "UPDATER_STATUS_FILE", tmp_path / "status.json")
    else:
        module, info = load("local_app_updater", "updater/local_app_updater.py"), dict(APP_INFO)
        monkeypatch.setattr(module, "UPDATER_STATUS_FILE", tmp_path / "status.json")
    monkeypatch.setattr(module, "read_heartbeat_update_info", lambda prefer_fresh_check=False: info)
    monkeypatch.setattr(module, "installed_version", lambda: "1.0.0")
    monkeypatch.setattr(module, "acquire_lock", lambda: None)
    monkeypatch.setattr(module, "UPDATE_HOLD_FILE", tmp_path / "update-hold")
    return module, info, request.param


def outcome(module, capsys, **kwargs):
    module.main(**kwargs)
    out = capsys.readouterr().out
    if "outside the idle window" in out and "deferring" in out:
        return "window"
    if "must not be interrupted" in out:
        return "held"
    if "lock held elsewhere" in out:
        return "applies"
    return out


def in_window(module, monkeypatch, inside):
    monkeypatch.setattr(module, "within_idle_window", lambda now=None: inside)


# --- overdue

@pytest.mark.parametrize("age, threshold, overdue", [
    (71, 72, False), (72, 72, True), (500, 72, True), (None, 72, False), ("x", 72, False), (True, 72, False),
    (100, None, True), (50, None, False),   # no threshold from the server -> the 72h default
    (100, 0, True), (100, -5, True), (100, "soon", True), (50, "soon", False),
])
def test_is_overdue(updater, age, threshold, overdue):
    module, _, kind = updater
    assert module.is_overdue({f"{kind}_update_age_hours": age, "update_overdue_after_hours": threshold}, kind) is overdue


def test_a_missing_age_is_not_overdue(updater):
    module, _, kind = updater
    assert module.is_overdue({}, kind) is False


def test_the_other_tiers_age_does_not_count(updater):
    module, _, kind = updater
    other = "app" if kind == "os" else "os"
    assert module.is_overdue({f"{other}_update_age_hours": 999, "update_overdue_after_hours": 72}, kind) is False


# --- the window and the overdue override

def test_a_fresh_update_waits_for_the_window(updater, monkeypatch, capsys):
    module, _, _ = updater
    in_window(module, monkeypatch, False)
    assert outcome(module, capsys) == "window"


def test_a_fresh_update_applies_inside_the_window(updater, monkeypatch, capsys):
    module, _, _ = updater
    in_window(module, monkeypatch, True)
    assert outcome(module, capsys) == "applies"


def test_an_overdue_update_ignores_the_window(updater, monkeypatch, capsys):
    module, info, kind = updater
    info[f"{kind}_update_age_hours"] = 80
    in_window(module, monkeypatch, False)
    assert outcome(module, capsys) == "applies"


# --- the hold

def hold(module, reasons, age_seconds=0):
    module.UPDATE_HOLD_FILE.write_text(json.dumps({"reasons": reasons}))
    stamp = module.UPDATE_HOLD_FILE.stat().st_mtime - age_seconds
    os.utime(module.UPDATE_HOLD_FILE, (stamp, stamp))


def test_a_hold_blocks_inside_the_window(updater, monkeypatch, capsys):
    module, _, _ = updater
    in_window(module, monkeypatch, True)
    hold(module, ["airshow_casting"])
    assert outcome(module, capsys) == "held"


def test_a_hold_blocks_an_overdue_update_too(updater, monkeypatch, capsys):
    module, info, kind = updater
    info[f"{kind}_update_age_hours"] = 500
    in_window(module, monkeypatch, False)
    hold(module, ["video_stream", "play_once_video"])
    assert outcome(module, capsys) == "held"


def test_force_ignores_the_hold(updater, monkeypatch, capsys):
    module, _, _ = updater
    in_window(module, monkeypatch, False)
    hold(module, ["airshow_casting"])
    assert outcome(module, capsys, force=True) == "applies"


def test_a_stale_hold_is_ignored(updater, monkeypatch, capsys):
    module, _, _ = updater
    in_window(module, monkeypatch, True)
    hold(module, ["airshow_casting"], age_seconds=module.UPDATE_HOLD_STALE_SECONDS + 30)
    assert outcome(module, capsys) == "applies"


def test_a_fresh_hold_is_honoured_up_to_the_stale_limit(updater):
    module, _, _ = updater
    hold(module, ["revelation"], age_seconds=module.UPDATE_HOLD_STALE_SECONDS - 30)
    assert module.update_hold_reasons() == ["revelation"]


@pytest.mark.parametrize("content", ["", "not json", "[]", '{"reasons": []}', '{"reasons": "x"}', '{"other": 1}', "null"])
def test_a_missing_or_malformed_hold_means_no_hold(updater, content):
    module, _, _ = updater
    assert module.update_hold_reasons() == []  # no file
    module.UPDATE_HOLD_FILE.write_text(content)
    assert module.update_hold_reasons() == []
