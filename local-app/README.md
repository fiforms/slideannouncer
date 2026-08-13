# local-app/

**Tier 2 — the on-device application.** Updated far more often than the OS
image, deployed via atomic symlink-swap rather than A/B partitioning (see
`updater/`).

- `backend/` — FastAPI app (`main.py`) served by `slide-announcer-backend.service`
  on loopback, reverse-proxied by nginx under `/api/`:
  - `GET /api/local/status` — hostname, `image_version`, and the
    `setup_mode`/`device_uuid` that `provisioning/firstboot.py`
    detected/generated, read from `/data/status/setup-mode.json`. Polled by
    the kiosk home page and by the slideshow's "needs attention" indicator.
  - `pairing.py`/`sync.py` — pairing (`POST /api/local/pair`,
    `POST /api/local/unpair`) and the slide sync daemon (background task,
    started in `main.py`'s lifespan alongside the heartbeat). `sync.py`
    maintains `/data/slides/{manifest,settings,active-playlist}.json` and
    the cached media under `/data/slides/media/` (nginx-aliased at
    `/media/`); `GET /api/local/slideshow` (`{playlist, settings}`) and
    `GET /api/local/sync/status` expose that state to the frontend.
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
  - `system_control.py` — reboot, OTA update-check, and factory reset,
    triggered from the Settings > System screen. See "Privileged operations
    from the web UI," below, for how this works without the backend ever
    running as root. `POST /api/local/system/reboot`,
    `GET`/`POST /api/local/system/update-check` (`POST` triggers a fresh
    check and returns it; `GET` returns the last cached result without
    triggering one), `POST /api/local/system/factory-reset`.
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
  - `/kiosk` — the actual slideshow renderer (`views/Slideshow.vue`), what
    `slide-announcer-kiosk.service`'s Chromium instance points at. Polls
    `GET /api/local/slideshow` (every 60s, matching the sync daemon's own
    cadence) for the playlist/settings and `GET /api/local/status` (every
    15s) for the "needs attention" corner indicator; crossfades through
    `active-playlist.json`'s slides at `settings.interval_seconds` (default
    10s), mirroring `resources/js/Components/SlideshowModal.vue`'s timing on
    the main website. No on-screen controls — this is unattended; Settings
    stays reachable only via the Menu remote button (`remoteNav.js`).
  - `/settings` — a smart-TV style settings menu (left-hand category rail
    + content pane), implementing `SLIDE_ANNOUNCER.md`'s "local settings
    menu" for real:
    - **Network** (first/default category) — connection status (WiFi vs.
      Ethernet vs. disconnected, SSID, signal, IP address), "Set Up
      Wi-Fi" (scans and lists nearby access points, select one, enter its
      password if secured, connect and see the result), and "Forget This
      Network."
    - **System** — "Check for Update" (triggers an OTA check, shows the
      raw result — reports "not paired" today, since pairing doesn't exist
      yet; that's the check actually working, not a bug), "Restart Device"
      (two-step confirm, then reboots), and "Factory Reset" (type-to-confirm,
      then wipes WiFi/pairing/cached slides/device identity and reboots
      into a state indistinguishable from a fresh SD card — see
      `../system/scripts/factory-reset-check.sh`).
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

## Privileged operations from the web UI

The backend runs unprivileged, as the dedicated `slideannouncer` system
user — no sudo, no setuid helper, no root shell anywhere in this process.
Two things it needs to trigger (rebooting, running an OTA update check)
genuinely require root, so instead of widening what the backend itself can
do, it's granted exactly two narrow capabilities via polkit (see
`../system/polkit/50-slide-announcer-system.rules`), the same pattern
already used for NetworkManager control:

- **`org.freedesktop.login1.reboot`** — lets `systemctl reboot`
  (`system_control.reboot()`) work, via `systemd-logind`'s own D-Bus
  action. Nothing else about system/session management is granted.
- **`org.freedesktop.systemd1.manage-units`, scoped to unit names matching
  `slide-announcer-*.service`** — lets the backend `systemctl start` its
  *own* on-demand units, never arbitrary system units. Two examples today:
  - `slide-announcer-update-check.service` (`system_control.trigger_update_check()`)
    runs `system/scripts/update-check.py` **as root** (the unit has no
    `User=`, so it defaults to root) to call `slide-announcer-update check`,
    then writes the result to `/data/status/update-check.json` — the
    unprivileged backend reads that file back rather than ever capturing a
    root process's output directly.
  - `slide-announcer-factory-reset-trigger.service` (`system_control.trigger_factory_reset()`)
    sets `/boot/firmware/FACTORY_RESET` and reboots. The actual reset work
    happens on the *next* boot, before `/data` is even mounted — see
    `system/scripts/factory-reset-check.sh` and `../system/README.md`'s
    entries on `slide-announcer-factory-reset-check.service` and
    `slide-announcer-data-dirs.service` for that half of the design.

This is the reusable shape for any future one-off privileged action
(installing an update, forcing tryboot, etc.): write a new
`slide-announcer-<name>.service` oneshot unit that does the actual root
work and records its result to a status file on `/data`, and it's already
covered by the existing polkit rule — no new grant needed, since the rule
matches on the naming convention rather than a specific unit.

**Not yet implemented** (Tier 2, per `SLIDE_ANNOUNCER.md`):
- Neither `pairing.py`, `sync.py`, nor the `/kiosk` slideshow renderer have
  a real device smoke test yet (see "Still needs a hands-on smoke test on
  the actual target Pi hardware" in `SLIDE_ANNOUNCER.md`'s "Kiosk display").
  No automated tests exist in this submodule at all currently.
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
