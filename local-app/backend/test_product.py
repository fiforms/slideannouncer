"""The product seam: core must work through product.get() alone."""
import asyncio

import httpx
import pytest

import heartbeat
import pairing
import product


def test_default_product_is_slideannouncer(monkeypatch):
    monkeypatch.delenv("KIOSK_PRODUCT", raising=False)
    monkeypatch.setattr(product, "PRODUCT_FILE", product.PRODUCT_FILE.with_name("no-such-PRODUCT"))
    assert product.product_name() == "slideannouncer"


def test_product_file_and_env_selection(tmp_path, monkeypatch):
    f = tmp_path / "PRODUCT"
    f.write_text("pos\n")
    monkeypatch.setattr(product, "PRODUCT_FILE", f)
    monkeypatch.delenv("KIOSK_PRODUCT", raising=False)
    assert product.product_name() == "pos"
    monkeypatch.setenv("KIOSK_PRODUCT", "other")
    assert product.product_name() == "other"


def test_api_url_joins_server_prefix_and_path(monkeypatch):
    monkeypatch.setattr(pairing, "read_server_url", lambda: "https://example.test")
    assert pairing.api_url("heartbeat") == "https://example.test/api/slide-announcers/heartbeat"
    assert pairing.api_url("/shows") == "https://example.test/api/slide-announcers/shows"


@pytest.fixture
def fake_product(monkeypatch):
    seen = {}
    fake = product.Product(
        name="fake",
        api_base="/api/devices",
        heartbeat_payload=lambda: {"print_queue": 3, "hostname": "evil", "app_version": "evil"},
        on_heartbeat_response=lambda response: seen.setdefault("response", response),
    )
    monkeypatch.setattr(product, "_loaded", fake)
    return seen


def test_heartbeat_merges_product_data_but_core_keys_win(monkeypatch, fake_product):
    sent = {}

    def server(request):
        import json
        sent["url"] = str(request.url)
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={"device_name": "d", "print_jobs": 2})

    transport = httpx.MockTransport(server)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
    monkeypatch.setattr(pairing, "read_device_token", lambda: "tok")
    monkeypatch.setattr(pairing, "read_server_url", lambda: "https://example.test")
    monkeypatch.setattr(pairing, "read_pending_language", lambda: None)
    monkeypatch.setattr(pairing, "write_device_name", lambda name: None)
    monkeypatch.setattr(pairing, "apply_server_language", lambda *a: None)
    monkeypatch.setattr(heartbeat, "_write_status", lambda data: None)

    asyncio.run(heartbeat.send_once())

    assert sent["url"] == "https://example.test/api/devices/heartbeat"
    assert sent["body"]["print_queue"] == 3
    assert sent["body"]["hostname"] != "evil" and sent["body"]["app_version"] != "evil"
    assert fake_product["response"]["print_jobs"] == 2


def test_unpair_wipes_product_paths(tmp_path, monkeypatch):
    mine = tmp_path / "product-state"
    mine.write_text("x")
    monkeypatch.setattr(pairing, "WIPE_PATHS", [])
    monkeypatch.setattr(product, "_loaded", product.Product(name="fake", api_base="/x", wipe_paths=[mine]))
    pairing.unpair_and_wipe()
    assert not mine.exists()
