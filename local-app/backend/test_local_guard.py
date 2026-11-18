"""local_guard: pages the kiosk loads from other sites must not be able to
drive /api/local/* (CSRF, DNS rebinding), while our own frontend, `npm run
dev` and loopback tools keep working."""
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import local_guard

OK = {"X-Local-App": "1"}


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(local_guard.LocalGuard)

    @app.get("/api/local/status")
    def status():
        return {"ok": True}

    @app.post("/api/local/system/factory-reset")
    def reset():
        return {"ok": True}

    @app.websocket("/api/local/stream")
    async def stream(ws: WebSocket):
        await ws.accept()
        await ws.send_text("hi")
        await ws.close()

    return TestClient(app, base_url="http://localhost")


@pytest.mark.parametrize("host", ["localhost", "localhost:80", "127.0.0.1:8000", "[::1]", "[::1]:80", "LOCALHOST"])
def test_loopback_hosts_are_served(client, host):
    assert client.get("/api/local/status", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "localhost.evil.example", "127.0.0.1.nip.io", "slideannouncer-1", "[::2]"])
def test_rebound_hosts_are_refused_even_for_reads(client, host):
    assert client.get("/api/local/status", headers={"Host": host}).status_code == 403


def test_our_frontend_post_passes(client):
    assert client.post("/api/local/system/factory-reset", headers={**OK, "Origin": "http://localhost"}).status_code == 200


def test_vite_dev_origin_passes(client):
    assert client.post("/api/local/system/factory-reset", headers={**OK, "Origin": "http://localhost:5173"}).status_code == 200


def test_loopback_tool_post_without_origin_passes(client):
    assert client.post("/api/local/system/factory-reset", headers=OK).status_code == 200


def test_form_style_post_without_header_is_refused(client):
    # What a cross-site <form method=post> or no-cors fetch can produce.
    r = client.post("/api/local/system/factory-reset", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_header_alone_does_not_excuse_foreign_origin(client):
    r = client.post("/api/local/system/factory-reset", headers={**OK, "Origin": "https://evil.example"})
    assert r.status_code == 403


@pytest.mark.parametrize("origin", ["null", "file://", "http://localhost.evil.example", "not a url"])
def test_odd_origins_are_refused(client, origin):
    assert client.post("/api/local/system/factory-reset", headers={**OK, "Origin": origin}).status_code == 403


def test_post_without_header_is_refused_even_same_origin(client):
    assert client.post("/api/local/system/factory-reset", headers={"Origin": "http://localhost"}).status_code == 403


def test_cross_origin_get_is_refused(client):
    assert client.get("/api/local/status", headers={"Origin": "https://evil.example"}).status_code == 403


def test_local_websocket_connects(client):
    with client.websocket_connect("ws://localhost/api/local/stream", headers={"Origin": "http://localhost"}) as ws:
        assert ws.receive_text() == "hi"


def test_foreign_websocket_is_refused(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("ws://localhost/api/local/stream", headers={"Origin": "https://evil.example"}) as ws:
            ws.receive_text()


def test_main_app_is_guarded():
    import main
    assert any(m.cls is local_guard.LocalGuard for m in main.app.user_middleware)
