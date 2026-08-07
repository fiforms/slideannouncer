# system/

systemd units, nginx config, and polkit rules that wire together the
compositor, kiosk Chromium, and `local-app/` services on the device.

## Contents

- `slide-announcer-data-resize.service` + `scripts/data-resize.sh` — grows
  the `/data` partition (partition 4) to fill the real SD card on first
  boot via `growpart`/`resize2fs`, since it's created as a small
  placeholder inside the built `.img` (see `../image-builder/repartition.sh`
  — "rest of the card" is only known once it's on real hardware). Runs
  before everything else; masks the stock Raspberry Pi OS root-resize unit,
  since `rootA` is a fixed size by design (the future RAUC A/B slot pair).
- `slide-announcer-firstboot.service` — runs
  `provisioning/firstboot.py` (SSH/machine-id regen, device identity check,
  setup-mode detection).
- `slide-announcer-backend.service` — the local backend (currently the
  stub in `local-app/backend/stub_main.py`; will become the real FastAPI
  app from `SLIDE_ANNOUNCER.md`'s Tier 2 design).
- `slide-announcer-kiosk.service` + `scripts/kiosk-start.sh` — starts
  `labwc` with Chromium autostarted in kiosk mode against the local
  nginx-served app. Runs as the dedicated `slideannouncer` user via
  `seatd`, no display manager/logind session involved.
- `nginx-slide-announcer.conf` — serves `local-app/frontend/stub` and
  reverse-proxies `/api/*` to the backend on loopback only.
- `polkit/50-networkmanager-slide-announcer.rules` — grants the
  `slideannouncer` service user NetworkManager D-Bus control. Unused by
  today's stub backend (no WiFi/pairing logic exists yet) — included now so
  Tier 2 doesn't need to revisit this.

**Not yet implemented:** the slide sync daemon and real kiosk slideshow
rendering (Tier 2), and anything RAUC-related (Tier 1 OTA).

See the main repo's `SLIDE_ANNOUNCER.md` for how these units interact across
update tiers (e.g. why local-app restarts are gated to an idle window).
