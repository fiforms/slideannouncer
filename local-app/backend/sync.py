"""Slide sync daemon — polls `GET /api/slide-announcers/slides` on a 60s
baseline (see SLIDE_ANNOUNCER.md, "Slide sync daemon") and maintains the
on-disk slide cache the kiosk frontend (frontend/src/views/Slideshow.vue,
served via `GET /api/local/slideshow` in main.py) renders from.

Runs as a background asyncio task inside this backend, alongside
heartbeat.py (same lifespan-task pattern in main.py, same "a bug here must
never take the backend down" rule) — not a separate systemd timer, for the
same reasons heartbeat.py gives.

The v1 sync endpoint (SlideAnnouncerSyncController::index()) returns no
`updated_at`/version field per slide — only `id, file_url, thumbnail_url,
mime_type, video_playback_mode, overlay_url, overlay_mime_type, sort_order,
expires_at`. "New/changed" is therefore detected by comparing `file_url`
(and, separately, `overlay_url`) against the manifest's last-known values
for that id: the app never mutates a slide's stored file in place without
changing its storage path (a re-upload is a new path), so a URL change is a
reliable proxy for "content changed" without a real version field.

`overlay_url` is a slide's optional 'slide-overlay' media (uploaded from the
admin/contributor Edit pages' Media Manager) — a future feature will
composite it on top of the base slide on-screen. This daemon only fetches
and caches it (as `<id>-overlay.<ext>`) so that feature has a file to work
with; the kiosk frontend does not render it yet.

Failure handling mirrors heartbeat.py: a network/timeout error leaves the
last-synced manifest, media, and settings on disk untouched (the kiosk
keeps showing cached slides) and only updates sync-status.json's
last_error. A 401 means the server explicitly rejected this device's
token (revoked/unpaired from the website) and triggers the same
wipe-and-reboot path as heartbeat.py's 401 handling.

Local `expires_at` is re-checked on every cycle — including ones where the
fetch itself failed or the device is unpaired — so a slide that expires
while the device is offline still drops out of the active playlist on
schedule ("so expiry still works offline" in SLIDE_ANNOUNCER.md).

active-playlist.json's media_url paths (`/media/<file>`) match
system/nginx-slide-announcer.conf's `location /media/ { alias
/data/slides/media/; }`, already provisioned ahead of this daemon existing.
"""
import asyncio
import json
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

import pairing
import system_control

INTERVAL_SECONDS = 60

SLIDES_DIR = Path("/data/slides")
MEDIA_DIR = SLIDES_DIR / "media"
MANIFEST_FILE = SLIDES_DIR / "manifest.json"
SETTINGS_FILE = SLIDES_DIR / "settings.json"
PLAYLIST_FILE = SLIDES_DIR / "active-playlist.json"
STATUS_FILE = Path("/data/status/sync-status.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, data) -> None:
    """Atomic write (temp file + os.replace) — same rationale as
    pairing.py's device-token write and heartbeat.py's status file: a
    reader (nginx serving media, the kiosk frontend, GET /api/local/status)
    must never observe a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data))
    tmp.chmod(0o644)
    os.replace(tmp, path)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _write_status(data: dict) -> None:
    _write_json(STATUS_FILE, data)


def read_status() -> dict:
    return _read_json(STATUS_FILE, {
        "implemented": True,
        "last_attempt_at": None,
        "last_success_at": None,
        "last_error": None,
    })


def read_playlist() -> list:
    return _read_json(PLAYLIST_FILE, [])


def read_settings() -> dict:
    return _read_json(SETTINGS_FILE, {})


def _local_filename(slide: dict, suffix: str = "", url_key: str = "file_url", mime_key: str = "mime_type") -> str:
    """<id><suffix>.<ext> — id-keyed so a changed URL for the same slide id
    overwrites the same on-disk file instead of accumulating orphans.
    `suffix` distinguishes a slide's base file (e.g. "3.jpg") from its
    optional overlay (e.g. "3-overlay.png")."""
    ext = Path(urlparse(slide[url_key]).path).suffix
    if not ext:
        ext = mimetypes.guess_extension(slide.get(mime_key) or "") or ""
    return f"{slide['id']}{suffix}{ext}"


def _is_expired(entry: dict, now: datetime) -> bool:
    expires_at = entry.get("expires_at")
    if not expires_at:
        return False
    return datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= now


async def _download(client: httpx.AsyncClient, url: str, dest: Path) -> None:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    async with client.stream("GET", url, timeout=30) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            async for chunk in resp.aiter_bytes():
                f.write(chunk)
    os.replace(tmp, dest)


def _build_active_playlist(manifest: dict) -> list:
    now = datetime.now(timezone.utc)
    entries = [
        {
            "id": entry["id"],
            "media_url": f"/media/{entry['local_filename']}",
            "mime_type": entry.get("mime_type"),
            "video_playback_mode": entry.get("video_playback_mode"),
            "overlay_media_url": f"/media/{entry['overlay_local_filename']}" if entry.get("overlay_local_filename") else None,
            "sort_order": entry.get("sort_order", 0),
        }
        for entry in manifest.values()
        if not _is_expired(entry, now) and (MEDIA_DIR / entry["local_filename"]).exists()
    ]
    entries.sort(key=lambda e: e["sort_order"])
    return entries


async def sync_once() -> None:
    """Runs one sync cycle. If unpaired, still rebuilds the active
    playlist from whatever manifest is on disk so local expiry keeps
    working even without a server round trip."""
    manifest = _read_json(MANIFEST_FILE, {})

    token = pairing.read_device_token()
    if not token:
        _write_json(PLAYLIST_FILE, _build_active_playlist(manifest))
        return

    server_url = pairing.read_server_url()

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{server_url}/api/slide-announcers/slides",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.RequestError as exc:
        _write_status({"last_attempt_at": _now_iso(), "last_error": str(exc)})
        _write_json(PLAYLIST_FILE, _build_active_playlist(manifest))
        return

    if resp.status_code == 401:
        pairing.unpair_and_wipe()
        _write_status({"last_attempt_at": _now_iso(), "last_error": "revoked"})
        await system_control.reboot()
        return

    if resp.status_code >= 400:
        _write_status({"last_attempt_at": _now_iso(), "last_error": f"HTTP {resp.status_code}"})
        _write_json(PLAYLIST_FILE, _build_active_playlist(manifest))
        return

    body = resp.json()
    slides = body.get("slides", [])
    settings = body.get("settings", {})

    seen_ids = set()
    new_manifest = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for slide in slides:
            slide_id = str(slide["id"])
            seen_ids.add(slide_id)
            local_filename = _local_filename(slide)
            previous = manifest.get(slide_id)
            changed = previous is None or previous.get("file_url") != slide["file_url"]

            if changed:
                try:
                    await _download(client, slide["file_url"], MEDIA_DIR / local_filename)
                except httpx.HTTPError:
                    # Keep whatever was already cached for this id rather
                    # than dropping the slide over one bad download — it'll
                    # retry next cycle since `changed` will still be true.
                    if previous:
                        new_manifest[slide_id] = previous
                    continue

            entry = {**slide, "id": slide_id, "local_filename": local_filename}

            # Optional 'slide-overlay' media — cached alongside the base
            # file so a future feature can composite it; not consumed by
            # the kiosk frontend yet.
            overlay_url = slide.get("overlay_url")
            if overlay_url:
                overlay_filename = _local_filename(slide, suffix="-overlay", url_key="overlay_url", mime_key="overlay_mime_type")
                overlay_changed = previous is None or previous.get("overlay_url") != overlay_url
                if overlay_changed:
                    try:
                        await _download(client, overlay_url, MEDIA_DIR / overlay_filename)
                        entry["overlay_local_filename"] = overlay_filename
                    except httpx.HTTPError:
                        # Same "keep the cached one, retry next cycle" rule
                        # as the base file above.
                        if previous and previous.get("overlay_local_filename"):
                            entry["overlay_local_filename"] = previous["overlay_local_filename"]
                else:
                    entry["overlay_local_filename"] = previous.get("overlay_local_filename")
            elif previous and previous.get("overlay_local_filename"):
                # Overlay was removed from this slide server-side — drop the
                # now-orphaned cached file.
                (MEDIA_DIR / previous["overlay_local_filename"]).unlink(missing_ok=True)

            new_manifest[slide_id] = entry

    # Slides that disappeared from the server response (deleted, expired,
    # or reassigned away from this entity) lose their cached media too.
    for slide_id, entry in manifest.items():
        if slide_id not in seen_ids:
            (MEDIA_DIR / entry["local_filename"]).unlink(missing_ok=True)
            if entry.get("overlay_local_filename"):
                (MEDIA_DIR / entry["overlay_local_filename"]).unlink(missing_ok=True)

    _write_json(MANIFEST_FILE, new_manifest)
    _write_json(SETTINGS_FILE, settings)
    _write_json(PLAYLIST_FILE, _build_active_playlist(new_manifest))
    _write_status({"last_attempt_at": _now_iso(), "last_success_at": _now_iso(), "last_error": None})


async def run_forever() -> None:
    while True:
        try:
            await sync_once()
        except Exception as exc:  # a bug here must never take the backend down
            _write_status({"last_attempt_at": _now_iso(), "last_error": f"unexpected: {exc}"})
        await asyncio.sleep(INTERVAL_SECONDS)
