# local-app/

**Tier 2 — the on-device application.** Updated far more often than the OS
image, deployed via atomic symlink-swap rather than A/B partitioning (see
`updater/`).

- `backend/` — FastAPI app (`main.py`) served by `slide-announcer-backend.service`
  on loopback, reverse-proxied by nginx under `/api/`:
  - `GET /api/local/status` — hostname, `image_version`, and the
    `setup_mode`/`device_uuid` that `provisioning/firstboot.py`
    detected/generated, read from `/data/status/setup-mode.json`. Polled by
    the kiosk home page.
  - `network.py` — NetworkManager control via `nmcli` subprocess calls,
    run as the `slideannouncer` user via the polkit rule in
    `../system/polkit/50-networkmanager-slide-announcer.rules`.
    `GET /api/local/network/status` (connection type, SSID, signal, IP
    address(es)), `GET /api/local/network/scan` (nearby access points),
    `POST /api/local/network/connect` (`{ssid, password}` — connects, then
    reports NetworkManager's own connectivity check result), and
    `POST /api/local/network/forget` (`{ssid}` — deletes the saved
    connection profile). These back the real on-device WiFi/network
    settings menu described below.
  - `requirements.txt` — installed into a venv at build time by
    `../image-builder/stage-slide-announcer/01-system-files/00-run.sh`.
- `frontend/` — a Vue 3 + Vite SPA (`npm run dev` for local development
  against a backend on `127.0.0.1:8000`; `npm run build` produces `dist/`,
  which is what actually ships on the device — see
  `../image-builder/build.sh`). Served by nginx at `/`, with an SPA
  fallback (`try_files ... /index.html`) so `vue-router`'s history mode
  works.
  - `/` — the kiosk home page: polls `/api/local/status` and shows
    hostname/device UUID/detected setup mode, plus a link into Settings.
  - `/settings` — a smart-TV style settings menu (left-hand category rail
    + content pane), implementing `SLIDE_ANNOUNCER.md`'s "local settings
    menu" for real:
    - **Network** (first/default category) — connection status (WiFi vs.
      Ethernet vs. disconnected, SSID, signal, IP address), "Set Up
      Wi-Fi" (scans and lists nearby access points, select one, enter its
      password if secured, connect and see the result), and "Forget This
      Network."
    - **About** — device info (hostname, image version, device UUID);
      placeholder for pairing/unpair status once pairing exists.
  - Navigation assumes the on-device remote is a keyboard+pointer HID
    combo (see `../provisioning/README.md`) — plain focusable
    links/buttons with visible focus rings, no custom spatial-navigation
    system or on-screen keyboard needed (per `SLIDE_ANNOUNCER.md`'s
    "First-boot / WiFi setup flow": "real key events into a real page").

## Versioning

`VERSION` (plain text, e.g. `0.1.0`) is the local-app release's semantic
version — bump it by hand for every release that should actually install
over whatever's already on a device (see "Installation on the device,"
below, for why only this X.Y.Z is ever compared). `package.sh` appends the
build's short git commit hash (and `-dirty` if the working tree wasn't
clean) to produce the full version string, e.g. `0.1.0-a1b2c3d`, the same
`<base>-<git-hash>[-dirty]` shape `image-builder/build.sh` uses for the OS
image's own version stamp.

## Building a release

```bash
./package.sh
```

Builds `frontend/` (Vue/Vite) and packages `backend/` (minus `venv/`) +
`frontend/dist/` + a `VERSION` file into
`deploy/slide-announcer-local-app-<version>.tar.gz`, plus a
`deploy/slide-announcer-local-app-latest.tar.gz` symlink and a
`deploy/VERSION` file pointing at it — the two things
`image-builder/build.sh` and the future `updater/` both consume. `deploy/`
is gitignored, same as `image-builder/deploy/`.

## Installation on the device

local-app is **never installed onto rootfs** — only its packaged tarball,
baked in read-only at `/opt/slide-announcer/local-app-release/` by
`image-builder/build.sh`/`00-run.sh`. The actual app lives entirely on
`/data`, using the same atomic symlink-swap layout `updater/` will later
maintain:

```
/data/local-app/
├── current -> releases/0.1.0-a1b2c3d/     (symlink; what's actually running)
└── releases/
    └── 0.1.0-a1b2c3d/
        ├── VERSION
        ├── backend/
        └── frontend/
```

`system/scripts/local-app-seed.py`
(`slide-announcer-local-app-seed.service`, run every boot, before
`slide-announcer-backend`/`slide-announcer-kiosk`) is what actually
extracts the embedded tarball onto `/data` and swaps the `current` symlink
— **only if `/data` has no local-app at all, or an older one than what's
baked into this image; it never downgrades.** This is deliberate, and
covers two cases with one mechanism:
- **Fresh card, or `/data` was reset/wiped** — nothing installed yet, so
  the embedded release seeds it unconditionally.
- **A RAUC OS update ships a newer local-app than what's on `/data`** —
  the device picks it up automatically on the reboot into the new slot, no
  separate app-update round trip needed. Conversely, if a live device
  already got a *newer* app from the (not yet built) OTA updater than
  what's baked into the OS image it's currently running, an OS update
  must never silently regress it back down — hence "never downgrades," not
  just "sync to whatever the image has."

Only the release's `X.Y.Z` (leading digits of `VERSION`, ignoring the
git-hash suffix) is ever compared — see `version_core()` in
`local-app-seed.py`. That means rebuilding the image without bumping
`local-app/VERSION` is correctly treated as "not newer," not re-extracted
on every single boot.

The Python venv the backend runs in (`/opt/slide-announcer/venv`) is
**fixed OS-image infrastructure**, built once at image-build time from the
release's `requirements.txt` — independent of whichever app release
`current` happens to point at. This means a future app-only update (via
`updater/`, no OS reflash) is expected to be code-only; a `requirements.txt`
change should ship alongside an OS update instead, since nothing currently
rebuilds the venv outside of `00-run.sh`.

**Not yet implemented** (Tier 2, per `SLIDE_ANNOUNCER.md`):
- `backend/pairing/` — exchanging the numeric one-time code for a Sanctum
  token via `POST /api/slide-announcers/pair`.
- `backend/sync/` — the slide sync daemon.
- `frontend/kiosk/` — the real slideshow renderer + "needs attention"
  overlay (today's home page is the pre-pairing status/settings screen,
  not the slideshow).
- The setup-mode-driven first-boot flows (headless config /
  HID-attached setup / AP-mode fallback) don't yet route into the
  Settings/Network screens above automatically — `provisioning/firstboot.py`
  only detects and records which mode applies; wiring the kiosk to actually
  launch into `/settings/network` on a fresh, unconfigured device is still
  open.
- `updater/local_app_updater.py` — the actual OTA app-update client (polls
  the heartbeat/version endpoint, downloads a release tarball, smoke-tests
  it, swaps `current`). `local-app-seed.py` implements the same
  extract-and-symlink-swap mechanism, but only ever triggered by an OS
  image boot, not a live poll against the server.

Runs as two separate systemd units today
(`slide-announcer-backend`, `slide-announcer-kiosk`) — the design's
eventual `slide-announcer-sync` unit (so a sync-loop crash can't take down
the WiFi/pairing API) lands with the real sync daemon.
