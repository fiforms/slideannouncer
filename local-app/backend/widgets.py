"""Overlay widgets on the kiosk — see the main app's
resources/widgets/README.md for what a widget is.

Two jobs, both kept off the kiosk page itself:

1. **Mirroring bundles.** The shows sync (sync.py) carries a `widgets`
   list: every bundle the synced slides place, with its version and file
   list. `mirror()` downloads each new slug/version into
   /data/slides/media/widgets/<slug>/<version>/ (staged in a temp dir,
   swapped in whole, so the kiosk never sees a half-written bundle) and
   records what's usable in index.json. It sits under the media dir so
   nginx's existing `/media/` location serves it: same loopback origin as
   the kiosk page, so `import()` needs no CORS, and no nginx (OS image)
   change is needed. Bundles keep working offline. A failed upgrade
   leaves the previous version in the index, still usable.

2. **Proxying data.** A widget's `api.fetch('events')` becomes
   `GET /api/local/widget-data/<overlay>/<element>/<endpoint>` here, which
   is forwarded to the server's token-authenticated widget-data endpoint.
   The server builds every upstream URL itself from the slide's saved
   parameters, so this device never fetches arbitrary addresses — which
   matters on a church LAN, where a device-side fetch of a user-chosen URL
   could reach printers and routers. Each good response is kept on disk,
   and served (marked stale) whenever the server can't be reached, so a
   calendar keeps showing its last events through an internet outage.

Both directories live under /data/slides, so pairing.unpair_and_wipe()
already clears them with the rest of the synced content.
"""
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

import httpx

import pairing

WIDGETS_DIR = Path("/data/slides/media/widgets")
INDEX_FILE = WIDGETS_DIR / "index.json"
DATA_CACHE_DIR = Path("/data/slides/widget-data")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
VERSION_RE = re.compile(r"^\d{1,5}\.\d{1,5}\.\d{1,5}([-+][0-9A-Za-z.-]{1,40})?$")
FILE_RE = re.compile(r"^[A-Za-z0-9._\-/@ ]+$")
ELEMENT_RE = re.compile(r"^as-el-\d{1,6}$")
ENDPOINT_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

# Passed straight back to the kiosk: the server's answer is final for these
# (e.g. a slide editor hasn't set the calendar address yet).
FINAL_STATUSES = {404, 422}
DATA_TIMEOUT_SECONDS = 15


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data))
    tmp.chmod(0o644)
    os.replace(tmp, path)


def read_index() -> dict:
    """{slug: {"version": ..., "entry": ...}} for every bundle that's fully
    on disk and usable."""
    try:
        return json.loads(INDEX_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _safe_relative(path: str) -> bool:
    return (
        bool(FILE_RE.match(path))
        and not path.startswith("/")
        and all(part not in ("", ".", "..") for part in path.split("/"))
    )


def _valid_bundle(bundle: dict, server_url: str) -> bool:
    files = bundle.get("files")
    return (
        isinstance(bundle.get("slug"), str) and bool(SLUG_RE.match(bundle["slug"]))
        and isinstance(bundle.get("version"), str) and bool(VERSION_RE.match(bundle["version"]))
        and isinstance(bundle.get("entry"), str)
        and isinstance(files, list) and files
        and all(
            isinstance(f, dict) and isinstance(f.get("path"), str) and _safe_relative(f["path"])
            # Only ever download from the server this device is paired to.
            and isinstance(f.get("url"), str) and f["url"].startswith(server_url.rstrip("/") + "/")
            for f in files
        )
        and bundle["entry"] in {f["path"] for f in files}
    )


async def _download_bundle(client: httpx.AsyncClient, bundle: dict, dest: Path) -> None:
    staging = WIDGETS_DIR / f".staging-{bundle['slug']}-{bundle['version']}"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        for f in bundle["files"]:
            target = staging / f["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            resp = await client.get(f["url"], timeout=30)
            resp.raise_for_status()
            target.write_bytes(resp.content)
            target.chmod(0o644)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(staging, dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


async def mirror(client: httpx.AsyncClient, bundles: list, server_url: str) -> dict:
    """Brings /data/slides/widgets in line with the sync's bundle list and
    returns the new index. Slugs no longer listed (no synced slide places
    them, or they were disabled server-side) are removed."""
    index = read_index()
    new_index = {}

    for bundle in bundles if isinstance(bundles, list) else []:
        if not isinstance(bundle, dict) or not _valid_bundle(bundle, server_url):
            continue
        slug, version = bundle["slug"], bundle["version"]
        dest = WIDGETS_DIR / slug / version
        if index.get(slug, {}).get("version") == version and dest.is_dir():
            new_index[slug] = index[slug]
            continue
        try:
            await _download_bundle(client, bundle, dest)
            new_index[slug] = {"version": version, "entry": bundle["entry"]}
        except (httpx.HTTPError, OSError):
            # Keep the previous version working; retried next sync.
            if slug in index and (WIDGETS_DIR / slug / index[slug]["version"]).is_dir():
                new_index[slug] = index[slug]

    _write_json(INDEX_FILE, new_index)

    # Drop slugs and versions the index no longer points at.
    if WIDGETS_DIR.is_dir():
        for slug_dir in WIDGETS_DIR.iterdir():
            if not slug_dir.is_dir() or slug_dir.name.startswith("."):
                continue
            keep = new_index.get(slug_dir.name, {}).get("version")
            if keep is None:
                shutil.rmtree(slug_dir, ignore_errors=True)
                continue
            for version_dir in slug_dir.iterdir():
                if version_dir.name != keep:
                    shutil.rmtree(version_dir, ignore_errors=True)

    return new_index


def placements_for_playlist(entry: dict, index: dict) -> list:
    """A synced slide's widget placements, ready for the kiosk: only those
    whose bundle is mirrored, pointed at the local copy and the local data
    proxy. The index's version wins over the placement's, so a widget
    whose upgrade hasn't downloaded yet runs its previous version."""
    overlay_id = entry.get("overlay_media_id")
    out = []
    for p in entry.get("widgets") or []:
        bundle = index.get(p.get("widget"))
        if not bundle or overlay_id is None:
            continue
        out.append({
            **p,
            "entry_url": f"/media/widgets/{p['widget']}/{bundle['version']}/{bundle['entry']}",
            "data_url": f"/api/local/widget-data/{overlay_id}/{p['id']}/__endpoint__",
        })
    return out


def _cache_file(overlay_id: int, element: str, endpoint: str) -> Path:
    key = hashlib.sha1(f"{overlay_id}/{element}/{endpoint}".encode()).hexdigest()
    return DATA_CACHE_DIR / f"{key}.json"


def _cached(path: Path):
    try:
        saved = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return {**saved["body"], "stale": True}


async def fetch_data(overlay_id: int, element: str, endpoint: str) -> tuple[int, dict]:
    """(status, JSON body) for the kiosk. Only well-formed ids are ever
    forwarded, and only to the paired server's widget-data endpoint."""
    if not ELEMENT_RE.match(element) or not ENDPOINT_RE.match(endpoint) or overlay_id < 1:
        return 404, {"error": "not_found"}

    cache = _cache_file(overlay_id, element, endpoint)
    token = pairing.read_device_token()
    if not token:
        return (200, cached) if (cached := _cached(cache)) else (503, {"error": "not_paired"})

    try:
        server_url = pairing.read_server_url()
        async with httpx.AsyncClient(timeout=DATA_TIMEOUT_SECONDS) as client:
            resp = await client.get(
                f"{server_url}/api/slide-announcers/widget-data/{overlay_id}/{element}/{endpoint}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        body = resp.json()
    except (httpx.HTTPError, pairing.PairingError, ValueError):
        resp, body = None, None

    if resp is not None and resp.status_code == 200 and isinstance(body, dict):
        _write_json(cache, {"saved_at": time.time(), "body": body})
        return 200, body
    if resp is not None and resp.status_code in FINAL_STATUSES:
        cache.unlink(missing_ok=True)
        return resp.status_code, body if isinstance(body, dict) else {"error": "unavailable"}

    # Offline, server error, rate limited, or token trouble (sync.py owns
    # revocation handling): fall back to the last good answer.
    if (cached := _cached(cache)) is not None:
        return 200, cached
    error = body.get("error") if isinstance(body, dict) else None
    return 502, {"error": error or "offline"}
