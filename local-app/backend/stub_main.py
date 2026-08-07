"""Stub local backend — proves the nginx -> FastAPI -> systemd wiring works
end-to-end on a freshly booted image. No WiFi/pairing/sync logic yet; see
SLIDE_ANNOUNCER.md, "Tier 2 — Local web app" for what replaces this.
"""
import json
import socket
from pathlib import Path

from fastapi import FastAPI

app = FastAPI()

SETUP_MODE_STATUS = Path("/data/status/setup-mode.json")
VERSION_FILE = Path("/opt/slide-announcer/VERSION")


@app.get("/api/local/status")
def local_status():
    setup_info = {}
    if SETUP_MODE_STATUS.exists():
        try:
            setup_info = json.loads(SETUP_MODE_STATUS.read_text())
        except json.JSONDecodeError:
            pass

    return {
        "status": "not_configured",
        "message": "Slide Announcer image booted successfully. Local app not yet installed.",
        "hostname": socket.gethostname(),
        "image_version": VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else None,
        "setup_mode": setup_info.get("setup_mode"),
        "device_uuid": setup_info.get("device_uuid"),
    }
