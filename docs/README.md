# docs/

Device-repo-specific documentation (build instructions, flashing/pairing
runbooks, troubleshooting) as it's written.

- [PRODUCTS.md](PRODUCTS.md) — the platform/product split: where a second kiosk product plugs in
  (image, backend, frontend).
- [DEVICE_CONTRACT.md](DEVICE_CONTRACT.md) — the pairing/heartbeat contract every product
  speaks to its management server, and how products extend it.
- [BUILDING.md](BUILDING.md) — building the image, flashing, pre-provisioning
  WiFi/identity, and what to expect on first boot.

The authoritative architecture design lives in the main `announcementslides`
repo's `SLIDE_ANNOUNCER.md`, since it documents both the server-side API
contract and the device-side design together — start there.
