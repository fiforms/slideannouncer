# Slide Announcer (device)

Raspberry Pi-based digital signage device for [AnnouncementSlides](https://github.com/fiforms/announcementslides).
A device pairs with a church/site (`Entity`) in the AnnouncementSlides server
and continuously syncs and displays that site's slide deck on a TV via
Chromium in kiosk mode, continuing to run from local cache through network
drops.

This repo is consumed as a git submodule at `slideannouncer/` in the main
`announcementslides` repo, which pins an exact commit of this repo per
server release.

**Status: early implementation, but the core loop is now confirmed
end-to-end on real hardware** — image build, first-boot provisioning,
device identity, on-device WiFi/network settings, pairing, slide sync,
and a first pass at RAUC OTA are all implemented; see each directory's
README for what's still rough (no automated tests yet, setup-mode flows
not auto-routed into Settings, the real OTA app-update client still to
come). Of the RAUC OTA paths, both the hotfix
mechanism (`image-builder/make-hotfix-bundle.sh`, 2026-08-10) and the
full-image/tryboot A/B path (2026-08-11) are now confirmed working
end-to-end on real hardware — install, tryboot reboot, and commit
(`os_prefix` flip + `mark-good`) all verified. Reconfirmed 2026-08-13 with
a real field OTA on a paired device (0.1.10 → 0.2.1): slideshow resumed
correctly post-update, and a subsequent power cycle stayed on 0.2.0,
confirming the commit persists across a normal reboot, not just the
tryboot boot itself.

**Confirmed 2026-08-15: a freshly-imaged device now boots all the way to
the kiosk display** (labwc + Chromium) on real hardware, not just an
already-provisioned one. Getting there fixed several concrete first-boot
bugs, none of them design changes — see `SLIDE_ANNOUNCER.md`'s "Kiosk
display" for specifics: `/boot/firmware` mounted read-only by default
(plus every writer that needed bracketing for it), `/data` formatted only
after its partition is grown to full size
rather than before (so `mke2fs` sizes block size/journal/inode density
correctly the first time, instead of an unfixable-after-the-fact 128MiB
placeholder), and two first-boot systemd ordering races (`growpart`'s own
`/tmp` scratch dir vs. `tmp.mount`, and `rpi-resize-swap-file.service`'s
fixed-size swapfile vs. `/data` actually being grown yet).

**Confirmed 2026-08-16: the full loop works, not just the boot.** Once
paired, the device displays that site's real slides synced from the
server on the kiosk display — not just a stub/placeholder screen. The
Menu key (or Esc) toggles between the live slideshow and the on-device
Settings screen.

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
