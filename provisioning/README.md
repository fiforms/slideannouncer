# provisioning/

`firstboot.py` — run by `system/slide-announcer-firstboot.service` on every
boot (not just the first — see below). Everything here is genuinely
functional, self-contained filesystem/crypto work with no dependency on the
not-yet-built pairing/sync API:

- **Once, ever** (guarded by `/data/.firstboot-complete`): regenerates SSH
  host keys and `machine-id`, so devices imaged from the same `.img` don't
  share either.
- **Every boot — device identity** (implements `SLIDE_ANNOUNCER.md`'s
  "Device identity & anti-clone protection" for real): recomputes
  `HMAC-SHA256(identity_key, device_uuid + mac_address)`
  (`identity_key` from `/data/identity.key`, `chmod 600`, never written to
  the boot partition) and compares against
  `/boot/firmware/slideannouncer.yaml`'s declared `device_uuid`/
  `device_uuid_check`. Any mismatch — hardware swap, a cloned SD card, or a
  hand-edited `device_uuid` without the matching secret — regenerates a
  fresh identity (`device_uuid` + `identity_key`) and clears any local
  pairing/slide state.
- **Every boot — setup-mode detection** (senses, does not act — acting
  requires the real Tier 2 backend, not yet built): checks for WiFi
  credentials in the boot YAML, probes for a usable keyboard + pointer HID
  device via `libinput list-devices`, and records which of the three setup
  paths (headless-config / HID-present / AP-mode-fallback) would apply to
  `/data/status/setup-mode.json` — which the stub local-app's
  `GET /api/local/status` reads and the stub kiosk page displays.

**Not yet implemented:** actually acting on the detected setup mode —
joining WiFi via `nmcli`, launching the on-device setup UI, AP-mode
hotspot, and the `POST /api/slide-announcers/pair` exchange. That's Tier 2
(`local-app/`), described in `SLIDE_ANNOUNCER.md`.
