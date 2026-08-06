# system/

systemd units, nginx config, and polkit rules that wire together the
compositor, kiosk Chromium, and `local-app/` services on the device.

Planned contents (not yet implemented):
- `slide-announcer-kiosk.service` — starts the compositor (labwc/cage) then
  execs Chromium in kiosk mode against the local nginx-served app.
- `slide-announcer-backend.service` — the WiFi/pairing FastAPI service.
- `slide-announcer-sync.service` — the slide sync daemon.
- `slide-announcer-firstboot.service` — one-shot: regenerates SSH host
  keys/machine-id, checks for known WiFi, enters AP mode if none.
- `nginx-slide-announcer.conf` — serves `local-app/frontend` static assets
  and locally-cached slide media, reverse-proxies `/api/*` to the backend on
  loopback only.
- `polkit/50-networkmanager-slide-announcer.rules` — grants the backend's
  dedicated non-root service user permission to drive NetworkManager over
  D-Bus, without running the backend as root.

See the main repo's `SLIDE_ANNOUNCER.md` for how these units interact across
update tiers (e.g. why local-app restarts are gated to an idle window).
