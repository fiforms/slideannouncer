# local-app/

**Tier 2 — the on-device application.** Updated far more often than the OS
image, deployed via atomic symlink-swap rather than A/B partitioning (see
`updater/`).

Planned contents (not yet implemented):
- `backend/` — FastAPI service(s):
  - `wifi/` — NetworkManager control via `nmcli`, AP/hotspot first-boot setup.
  - `pairing/` — exchanges the numeric one-time code for a Sanctum token via
    the server's `POST /api/slide-announcers/pair`.
  - `sync/` — the slide sync daemon: polls
    `GET /api/slide-announcers/slides`, diffs against a local manifest,
    atomically replaces changed files, prunes expired/removed slides.
  - `status/` — the local-only `GET /api/local/status` endpoint the kiosk
    frontend polls to decide whether to show the "needs attention" overlay
    (loopback only, so it works offline).
  - `main.py` — service entrypoint.
- `frontend/`:
  - `setup/` — WiFi + pairing screens, served during first-boot/AP mode.
  - `kiosk/` — the slideshow renderer + always-on-top "needs attention"
    overlay, served once paired.
- `VERSION` — current local-app version, checked by `updater/`.
- `package.sh` — builds the release tarball consumed by `updater/`.

Runs as two separate systemd units (`slide-announcer-backend`,
`slide-announcer-sync`) so a sync-loop crash can't take down the WiFi/pairing
API and vice versa. See the main repo's `SLIDE_ANNOUNCER.md`, "Tier 2 — Local
web app" and "Slide sync daemon" sections for full rationale.
