"""Tests for widgets.py — bundle mirroring and the offline-tolerant data
proxy. No network: the server is an httpx.MockTransport, and every /data
path is redirected into pytest's tmp_path.
"""
import asyncio
import json

import httpx
import pytest

import pairing
from products.slideannouncer import widgets

SERVER = "https://slides.example.org"
REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(widgets, "WIDGETS_DIR", tmp_path / "media" / "widgets")
    monkeypatch.setattr(widgets, "INDEX_FILE", tmp_path / "media" / "widgets" / "index.json")
    monkeypatch.setattr(widgets, "DATA_CACHE_DIR", tmp_path / "widget-data")
    monkeypatch.setattr(pairing, "read_device_token", lambda: "tok")
    monkeypatch.setattr(pairing, "read_server_url", lambda: SERVER)
    return tmp_path


def bundle(version="1.0.0", files=("manifest.json", "widget.js", "icon.png"), slug="calendar"):
    return {
        "slug": slug, "version": version, "entry": "widget.js",
        "files": [{"path": f, "url": f"{SERVER}/widget-assets/{slug}/{version}/{f}"} for f in files],
    }


def serve(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def assets(request):
    return httpx.Response(200, content=f"content of {request.url.path}".encode())


def run_mirror(bundles, handler=assets):
    async def go():
        async with serve(handler) as client:
            return await widgets.mirror(client, bundles, SERVER)
    return asyncio.run(go())


# ── mirroring ──────────────────────────────────────────────────────────────


def test_mirror_downloads_a_bundle_and_indexes_it():
    index = run_mirror([bundle()])

    assert index == {"calendar": {"version": "1.0.0", "entry": "widget.js"}}
    js = widgets.WIDGETS_DIR / "calendar" / "1.0.0" / "widget.js"
    assert js.read_text() == "content of /widget-assets/calendar/1.0.0/widget.js"
    assert widgets.read_index() == index


def test_an_unchanged_bundle_is_not_downloaded_again():
    run_mirror([bundle()])
    calls = []

    def counting(request):
        calls.append(request.url)
        return assets(request)

    run_mirror([bundle()], counting)
    assert calls == []


def test_upgrade_replaces_the_old_version():
    run_mirror([bundle("1.0.0")])
    run_mirror([bundle("1.1.0")])

    assert widgets.read_index()["calendar"]["version"] == "1.1.0"
    assert not (widgets.WIDGETS_DIR / "calendar" / "1.0.0").exists()
    assert (widgets.WIDGETS_DIR / "calendar" / "1.1.0" / "widget.js").exists()


def test_a_failed_upgrade_keeps_the_previous_version_working():
    run_mirror([bundle("1.0.0")])

    def broken(request):
        return httpx.Response(500) if "1.1.0" in request.url.path else assets(request)

    index = run_mirror([bundle("1.1.0")], broken)

    assert index["calendar"]["version"] == "1.0.0"
    assert (widgets.WIDGETS_DIR / "calendar" / "1.0.0" / "widget.js").exists()
    assert not any(p.name.startswith(".staging") for p in widgets.WIDGETS_DIR.iterdir())


def test_widgets_no_longer_synced_are_removed():
    run_mirror([bundle(), bundle(slug="clock")])
    run_mirror([bundle(slug="clock")])

    assert set(widgets.read_index()) == {"clock"}
    assert not (widgets.WIDGETS_DIR / "calendar").exists()


@pytest.mark.parametrize("bad", [
    {**bundle(), "slug": "../evil"},
    {**bundle(), "version": "1.0.0/../../x"},
    bundle(files=("../../etc/passwd", "widget.js")),
    bundle(files=("/abs.js", "widget.js")),
    bundle(files=("a/./b.js", "widget.js")),
    {**bundle(), "files": [{"path": "widget.js", "url": "https://evil.example/widget.js"}]},
    {**bundle(), "entry": "missing.js"},
])
def test_unsafe_or_foreign_bundles_are_ignored(bad, tmp_path):
    calls = []

    def counting(request):
        calls.append(request.url)
        return assets(request)

    assert run_mirror([bad], counting) == {}
    assert calls == []
    assert not (tmp_path / "etc").exists()


def test_playlist_placements_point_at_local_copies_and_skip_unmirrored():
    entry = {
        "overlay_media_id": 42,
        "widgets": [
            {"id": "as-el-1", "widget": "calendar", "version": "1.1.0", "x": 0, "y": 0, "w": 10, "h": 10, "params": {}},
            {"id": "as-el-2", "widget": "clock", "version": "1.0.0", "x": 0, "y": 0, "w": 10, "h": 10, "params": {}},
        ],
    }
    # calendar 1.1.0 hasn't downloaded yet — the mirrored 1.0.0 is used.
    placements = widgets.placements_for_playlist(entry, {"calendar": {"version": "1.0.0", "entry": "widget.js"}})

    assert [p["id"] for p in placements] == ["as-el-1"]
    assert placements[0]["entry_url"] == "/media/widgets/calendar/1.0.0/widget.js"
    assert placements[0]["data_url"] == "/api/local/widget-data/42/as-el-1/__endpoint__"


# ── data proxy ─────────────────────────────────────────────────────────────


def fetch(monkeypatch, handler, *args):
    monkeypatch.setattr(widgets.httpx, "AsyncClient", lambda **kw: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kw))
    return asyncio.run(widgets.fetch_data(*args))


def test_data_is_forwarded_by_reference_with_the_device_token(monkeypatch):
    seen = {}

    def server(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"data": {"name": "Conf"}, "fetched_at": 1, "stale": False})

    status, body = fetch(monkeypatch, server, 42, "as-el-1", "events")

    assert status == 200 and body["data"]["name"] == "Conf"
    assert seen["url"] == f"{SERVER}/api/slide-announcers/widget-data/42/as-el-1/events"
    assert seen["auth"] == "Bearer tok"


def test_the_last_good_answer_is_served_while_offline(monkeypatch):
    fetch(monkeypatch, lambda r: httpx.Response(200, json={"data": {"name": "Conf"}, "stale": False}), 42, "as-el-1", "events")

    def offline(request):
        raise httpx.ConnectError("no route to host")

    status, body = fetch(monkeypatch, offline, 42, "as-el-1", "events")
    assert status == 200
    assert body["data"]["name"] == "Conf" and body["stale"] is True

    status, body = fetch(monkeypatch, lambda r: httpx.Response(500, json={}), 42, "as-el-1", "events")
    assert status == 200 and body["stale"] is True


def test_offline_with_nothing_cached_reports_offline(monkeypatch):
    def offline(request):
        raise httpx.ConnectError("down")

    assert fetch(monkeypatch, offline, 42, "as-el-1", "events") == (502, {"error": "offline"})


def test_final_answers_pass_through_and_drop_the_cache(monkeypatch):
    fetch(monkeypatch, lambda r: httpx.Response(200, json={"data": 1}), 42, "as-el-1", "events")

    status, body = fetch(monkeypatch, lambda r: httpx.Response(422, json={"error": "not_configured"}), 42, "as-el-1", "events")
    assert (status, body) == (422, {"error": "not_configured"})

    def offline(request):
        raise httpx.ConnectError("down")

    assert fetch(monkeypatch, offline, 42, "as-el-1", "events")[0] == 502


@pytest.mark.parametrize("args", [(42, "../../x", "events"), (42, "as-el-1", "../shows"), (0, "as-el-1", "events"), (42, "as-el-1", "Events")])
def test_malformed_references_are_never_forwarded(monkeypatch, args):
    calls = []

    def server(request):
        calls.append(request.url)
        return httpx.Response(200, json={})

    assert fetch(monkeypatch, server, *args)[0] == 404
    assert calls == []


def test_runtime_args_are_forwarded_and_cached_separately(monkeypatch):
    seen = []

    def server(request):
        seen.append(dict(request.url.params))
        lat = request.url.params.get("args[lat]")
        return httpx.Response(200, json={"data": {"lat": lat}, "stale": False})

    items = [("args[lat]", "34.07"), ("args[lon]", "-118.4"), ("args[../x]", "1"), ("other", "y")]
    status, body = fetch(monkeypatch, server, 42, "as-el-1", "forecast", items)
    assert status == 200 and body["data"]["lat"] == "34.07"
    assert seen == [{"args[lat]": "34.07", "args[lon]": "-118.4"}]

    fetch(monkeypatch, server, 42, "as-el-1", "forecast", [("args[lat]", "35.5"), ("args[lon]", "-80")])

    def offline(request):
        raise httpx.ConnectError("down")

    # Each lat/lon keeps its own last-good copy.
    assert fetch(monkeypatch, offline, 42, "as-el-1", "forecast", items)[1]["data"]["lat"] == "34.07"
    assert fetch(monkeypatch, offline, 42, "as-el-1", "forecast", [("args[lon]", "-80"), ("args[lat]", "35.5")])[1]["data"]["lat"] == "35.5"
