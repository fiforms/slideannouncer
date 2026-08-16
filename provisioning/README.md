# provisioning/

`firstboot.py` — run by `system/slide-announcer-firstboot.service` on every
boot (not just the first — see below). Self-contained filesystem/crypto/
local-NetworkManager-query work; no calls to the AnnouncementSlides server
itself:

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
- **Every boot — setup-mode detection** (senses, does not act on the result
  itself — that's the real local-app backend's job, see below): polls
  NetworkManager for an already-active wifi/ethernet connection (i.e. WiFi
  pre-provisioned via cloud-init's `network-config` on the boot partition
  already connected — see `docs/BUILDING.md`'s "Pre-provisioning network +
  identity"), probes for a usable keyboard + pointer HID device via
  `libinput list-devices`, and records which of the three setup paths
  (headless-config / HID-present / AP-mode-fallback) applies to
  `/data/status/setup-mode.json` — which the local-app backend's
  `GET /api/local/status` reads and the kiosk home page displays. WiFi
  credentials themselves never live in `slideannouncer.yaml` — that file is
  identity + initial-setup hints only; see `network-config` for network
  settings.

Pairing, slide sync, and the on-device Settings/Network UI (Tier 2,
`local-app/`) are implemented and confirmed working on real hardware — see
the top-level `README.md`'s status section and `SLIDE_ANNOUNCER.md`.
