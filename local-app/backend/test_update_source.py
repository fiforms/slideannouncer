"""The "look for software elsewhere" pointer in the heartbeat response (heartbeat.apply_update_source)."""
import asyncio

import httpx
import pytest

import heartbeat

run = asyncio.run

PAYLOAD = {"architecture": "aarch64", "app_version": "1.0.0", "os_version": "2.0.0_1.0.0"}
BASE = {"ok": True, "device_name": "Dock", "update_channel": "testing", "update_server_url": "https://master.example.org/",
        "app_update_available": True, "app_download_url": "https://local/own-offer"}


def with_transport(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(heartbeat.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_a_response_without_a_pointer_is_untouched():
    response = {"ok": True, "app_update_available": True}

    assert run(heartbeat.apply_update_source(response, PAYLOAD)) is response


def test_the_source_is_asked_with_the_devices_versions_and_channel_and_only_update_keys_are_taken(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), request.read()
        return httpx.Response(200, json={
            "app_update_available": True, "latest_app_version": "1.1.0", "app_download_url": "https://cdn/app.tgz", "app_sha256": "ab",
            "device_name": "HIJACKED", "kiosk_function": "evil"})

    with_transport(monkeypatch, handler)

    out = run(heartbeat.apply_update_source(dict(BASE), PAYLOAD))

    assert seen["url"] == "https://master.example.org/api/devices/update-check"
    assert b'"channel":"testing"' in seen["body"].replace(b" ", b"") and b"aarch64" in seen["body"]
    assert out["latest_app_version"] == "1.1.0" and out["app_download_url"] == "https://cdn/app.tgz"
    assert out["device_name"] == "Dock" and "kiosk_function" not in out
    assert out["update_source"] == "https://master.example.org/"
    assert out["os_update_available"] is False  # the source said nothing about the OS


def test_an_unreachable_source_means_no_update_not_a_fallback(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("down")

    with_transport(monkeypatch, handler)

    out = run(heartbeat.apply_update_source(dict(BASE), PAYLOAD))

    assert out["app_update_available"] is False and out["app_download_url"] is None
    assert "down" in out["update_source_error"]


@pytest.mark.parametrize("source", ["file:///etc/passwd", "ftp://x", "javascript:alert(1)", 5])
def test_only_http_sources_are_followed(source):
    out = run(heartbeat.apply_update_source({**BASE, "update_server_url": source}, PAYLOAD))

    assert out["app_update_available"] is False and out["update_source_error"]


# --- the standalone update CLI (system/scripts/rauc-update.py) carries its own copy of the rule

import importlib.machinery
import importlib.util
import os

ROOT = os.environ.get("PLATFORM_ROOT")
needs_root = pytest.mark.skipif(not ROOT, reason="PLATFORM_ROOT not set (run through local-app/run-tests.sh)")


def load_cli():
    loader = importlib.machinery.SourceFileLoader("rauc_update_cli", f"{ROOT}/system/scripts/rauc-update.py")
    cli = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(cli)
    return cli


@needs_root
def test_cli_follows_the_pointer_the_same_way_as_the_backend():
    cli = load_cli()
    seen = {}

    def post(url, body):
        seen.update(url=url, body=body)
        return {"os_update_available": True, "latest_os_version": "2.0.0", "os_bundle_url": "https://cdn/os.raucb", "device_name": "HIJACKED"}

    out = cli.apply_update_source(dict(BASE), PAYLOAD, post=post)

    assert seen["url"] == "https://master.example.org/api/devices/update-check"
    assert seen["body"] == {**PAYLOAD, "channel": "testing"}
    assert out["os_bundle_url"] == "https://cdn/os.raucb" and out["device_name"] == "Dock"
    assert out["app_update_available"] is False  # the source said nothing about the app


@needs_root
def test_cli_treats_a_failing_or_non_http_source_as_no_update():
    cli = load_cli()

    def down(url, body):
        raise OSError("connection refused")

    assert cli.apply_update_source(dict(BASE), PAYLOAD, post=down)["os_update_available"] is False
    out = cli.apply_update_source({**BASE, "update_server_url": "file:///etc/passwd"}, PAYLOAD, post=lambda *a: {"os_update_available": True})
    assert out["os_update_available"] is False and out["update_source_error"]
    assert cli.apply_update_source({"os_update_available": True}, PAYLOAD) == {"os_update_available": True}  # no pointer: untouched


@needs_root
def test_cli_and_backend_agree_on_which_fields_a_source_may_set():
    assert load_cli().UPDATE_KEYS == heartbeat.UPDATE_KEYS


@needs_root
def test_cli_uses_the_products_api_base_when_the_backend_has_published_it(tmp_path, monkeypatch):
    cli = load_cli()
    monkeypatch.setattr(cli, "API_BASE_FILE", tmp_path / "api-base")

    assert cli.read_api_base() == "/api/slide-announcers"  # nothing published yet: the legacy default
    (tmp_path / "api-base").write_text("/api/devices\n")
    assert cli.read_api_base() == "/api/devices"


def test_the_backend_publishes_its_api_base_for_the_cli(tmp_path, monkeypatch):
    import product

    monkeypatch.setattr(heartbeat, "API_BASE_FILE", tmp_path / "status" / "api-base")
    monkeypatch.setattr(product, "_loaded", product.Product(name="fake", api_base="/api/devices"))

    heartbeat._publish_api_base()

    assert (tmp_path / "status" / "api-base").read_text() == "/api/devices\n"
