# Kiosk platform (Raspberry Pi)

A platform for single-purpose, network-managed kiosks on Raspberry Pi: a
signed, self-updating OS image that boots straight into a full-screen
Chromium page, with everything a fleet of unattended devices needs already
built in. You supply the product — what the screen actually shows and does —
and reuse the rest.

**What the platform provides**

- **OS image** — pi-gen-based Raspberry Pi OS (Chromium in kiosk mode,
  compositor, NetworkManager, nginx, RAUC), built and packaged as a signed
  RAUC OTA bundle.
- **First-boot provisioning and identity** — stable device identity, hostname,
  pre-provisioned WiFi, and an AP-mode captive-portal setup flow.
- **On-device settings shell** — WiFi/network diagnostics, audio, screen
  resolution, language, PIN lock, remote-control navigation.
- **Pairing and heartbeat** — a device pairs to a management server with a
  short code, then reports status and receives settings and update offers
  every few minutes. The wire format is the
  [device management contract](docs/DEVICE_CONTRACT.md), so any server that
  implements it can manage devices.
- **Two-tier updates** — the base OS via RAUC A/B with tryboot, health check
  and automatic rollback (plus small live-root hotfixes), and the local app
  via an atomic symlink swap with automatic revert. Both are self-hosted
  from the management server.
- **Offline resilience** — a device keeps running from its local cache through
  network drops.

**What a product provides** — see [docs/PRODUCTS.md](docs/PRODUCTS.md): extra
OS packages/units, a backend Python package (sync loops, its own endpoints),
and a Vue frontend (the display and its settings pages). A product is one
directory outside this repo, selected with `PRODUCT_ROOT=…` at build time.
[`examples/portal/`](examples/portal/) is a minimal complete one; copy it to
start a new product. A product extends the heartbeat with its own namespaced
keys and its own endpoints under the same server API prefix (see the
contract's *Extensions* section).

## Products built on it

- **Slide Announcer** — the original product, and the reason the platform
  exists: distributes announcement slides to Seventh-day Adventist churches
  from the [AnnouncementSlides](https://github.com/fiforms/announcementslides)
  server. A device pairs with a church/site, continuously syncs that site's
  slide shows, and displays them on a TV. It lives in the AnnouncementSlides
  repo at `kiosk-products/slideannouncer/`, where this repo is consumed as a
  git submodule at `slideannouncer/` (pinned to an exact commit per server
  release). AnnouncementSlides' Laravel app is also the reference
  implementation of the management server. The platform's name and many
  internal identifiers (`slide-announcer-*` services, `slideannouncer.yaml`,
  the `slideannouncer` user) still carry this origin; they are historical, not
  a limit on what the platform can run.

Other kinds of kiosk — a point-of-sale terminal, a pointed-at-a-web-app
display, an info board — fit the same shape: the platform stays, only the
product directory (and its server half) changes.

## Status

Early implementation, confirmed end-to-end on real hardware: image build,
first-boot provisioning, device identity, on-device WiFi/network settings,
pairing, heartbeat, a freshly imaged device booting all the way to the kiosk
display, and both update tiers from a single click in the on-device Settings
(OS hotfix, full-image A/B install with tryboot reboot and commit surviving a
power cycle, and local-app update). See each directory's README for what is
still rough (setup-mode flows not auto-routed into Settings; some features,
noted in their own docs, not yet hardware-tested).

The wider architecture write-up — update tiers, device identity and
anti-clone protection, first-boot flow, and the reasoning behind each choice —
is `SLIDE_ANNOUNCER.md` in the AnnouncementSlides repo; it describes the
platform as used by the Slide Announcer product.

## Layout

- [`image-builder/`](image-builder/) — the pi-gen pipeline that builds the
  base image and packages signed RAUC bundles and hotfixes.
- [`local-app/`](local-app/) — the on-device backend (WiFi setup, pairing,
  heartbeat, update orchestration) and frontend (setup screens, Settings,
  and the host for the product's display).
- [`system/`](system/) — systemd units, nginx config, and polkit rules tying
  the above together on the device.
- [`updater/`](updater/) — the local-app self-update client (atomic
  symlink-swap deploys, independent of the OS-level OTA tier).
- [`provisioning/`](provisioning/) — first-boot and AP-mode WiFi setup
  scripts.
- [`examples/portal/`](examples/portal/) — a minimal example product.
- [`docs/`](docs/) — [products](docs/PRODUCTS.md),
  [device contract](docs/DEVICE_CONTRACT.md) (with an
  [OpenAPI file](docs/device-contract.openapi.yaml)), and
  [building](docs/BUILDING.md).
- Feature notes for platform features: [audio](AUDIO_IMPLEMENTATION.md),
  [screens/resolution](DISPLAY_IMPLEMENTATION.md),
  [localization](LOCALIZATION_TODO.md).

## Update tiers

| Tier | What | Cadence | Mechanism |
|---|---|---|---|
| 1 | Base OS image | Rare | RAUC A/B OTA (self-hosted by the management server), atomic + auto-rollback |
| 2 | Local web app (platform + product code) | Frequent | Atomic symlink-swap deploy over HTTP |
| 3 | Product content (e.g. slides) | Continuous | Product-defined sync against the product's server endpoints; not part of the platform |
