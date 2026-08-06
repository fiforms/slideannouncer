# Slide Announcer (device)

Raspberry Pi-based digital signage device for [AnnouncementSlides](https://github.com/fiforms/announcementslides).
A device pairs with a church/site (`Entity`) in the AnnouncementSlides server
and continuously syncs and displays that site's slide deck on a TV via
Chromium in kiosk mode, continuing to run from local cache through network
drops.

This repo is consumed as a git submodule at `slideannouncer/` in the main
`announcementslides` repo, which pins an exact commit of this repo per
server release.

**Status: architecture/scaffolding stage — no implementation yet.**

See [`SLIDE_ANNOUNCER.md`](https://github.com/fiforms/announcementslides/blob/master/SLIDE_ANNOUNCER.md)
in the main repo for the full design: the three update tiers (OS image,
local app, slide content), the pairing/sync API contract this device talks
to, and the rationale behind each architectural choice below.

## Layout

- [`image-builder/`](image-builder/) — pi-gen-based pipeline that produces
  the base Raspberry Pi OS image (Chromium, compositor, NetworkManager,
  nginx, RAUC client) and packages it as a signed RAUC OTA bundle.
- [`local-app/`](local-app/) — the on-device backend (WiFi setup, pairing,
  slide sync daemon) and frontend (setup screens + kiosk slideshow UI).
- [`system/`](system/) — systemd units, nginx config, and polkit rules tying
  the above together on the device.
- [`updater/`](updater/) — the local-app self-update client (atomic
  symlink-swap deploys, independent of the OS-level OTA tier).
- [`provisioning/`](provisioning/) — first-boot and AP-mode WiFi setup
  scripts.
- [`docs/`](docs/) — device-repo-specific documentation.

## Update tiers

| Tier | What | Cadence | Mechanism |
|---|---|---|---|
| 1 | Base OS image | Rare | RAUC A/B OTA (self-hosted via the Laravel app), atomic + auto-rollback |
| 2 | Local web app | Frequent | Atomic symlink-swap deploy over HTTP |
| 3 | Slide content | Continuous | Polling sync against the server API |
