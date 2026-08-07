# local-app/

**Tier 2 — the on-device application.** Updated far more often than the OS
image, deployed via atomic symlink-swap rather than A/B partitioning (see
`updater/`).

**Currently a stub**, just enough to prove the boot → firstboot → kiosk →
local-API pipeline wires together end-to-end on real hardware, ahead of the
real WiFi/pairing/sync/kiosk implementation:

- `backend/stub_main.py` — minimal FastAPI app. `GET /api/local/status`
  returns hostname, and the `setup_mode`/`device_uuid` that
  `provisioning/firstboot.py` detected/generated, read from
  `/data/status/setup-mode.json`. This *is* the real shape of the
  local-status endpoint from `SLIDE_ANNOUNCER.md`'s design — proves the
  nginx → FastAPI → systemd wiring, not a fake.
- `backend/requirements.txt` — installed into a venv at build time by
  `../image-builder/stage-slide-announcer/01-system-files/00-run.sh`.
- `frontend/stub/index.html` — the page the kiosk actually displays: polls
  `/api/local/status` and shows hostname/device UUID/detected setup mode.
  This is the visible proof, on the TV, that the pipeline works.

**Not yet implemented** (Tier 2, per `SLIDE_ANNOUNCER.md`):
- `backend/wifi/` — NetworkManager control via `nmcli`, AP/hotspot
  first-boot setup, parsing WiFi credentials from
  `/boot/firmware/slideannouncer.yaml`.
- `backend/pairing/` — exchanging the numeric one-time code for a Sanctum
  token via `POST /api/slide-announcers/pair`.
- `backend/sync/` — the slide sync daemon.
- `frontend/setup/` — the real WiFi + pairing screens.
- `frontend/kiosk/` — the real slideshow renderer + "needs attention"
  overlay.
- `package.sh` — the release-tarball build script `updater/` will consume.

Runs as two separate systemd units today
(`slide-announcer-backend`, `slide-announcer-kiosk`) — the design's
eventual `slide-announcer-sync` unit (so a sync-loop crash can't take down
the WiFi/pairing API) lands with the real sync daemon.
