# docs/

Device-repo-specific documentation (build instructions, flashing/pairing
runbooks, troubleshooting) as it's written.

- [PRODUCTS.md](PRODUCTS.md) — the platform/product split: where a second kiosk product plugs in
  (image, backend, frontend).
- [DEVICE_CONTRACT.md](DEVICE_CONTRACT.md) — the core management contract (identity, pairing,
  heartbeat, updates) every product speaks to its management server, and how products extend it.
  Enough to build a second management console from.
  [device-contract.openapi.yaml](device-contract.openapi.yaml) is the machine-readable wire format.
- [BUILDING.md](BUILDING.md) — building the image, flashing, pre-provisioning
  WiFi/identity, and what to expect on first boot.

The device-management wire contract is authoritative here, in
`DEVICE_CONTRACT.md`. The main `announcementslides` repo's `SLIDE_ANNOUNCER.md`
holds the wider architecture design and the signage-specific server API.
