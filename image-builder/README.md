# image-builder/

**Tier 1 — base OS image.** Produces the rarely-updated Raspberry Pi OS
image and packages it as a signed RAUC A/B OTA bundle, self-hosted from the
Laravel app rather than a separate OTA product.

Planned contents (not yet implemented):
- `pi-gen/` — pinned submodule/checkout of the official [pi-gen](https://github.com/RPi-Distro/pi-gen) builder.
- `stage-slide-announcer/` — custom pi-gen stage injecting Chromium, a
  minimal compositor (labwc), NetworkManager, nginx, the `rauc` client, and
  this repo's `system/` units/polkit rules.
- `stage-slide-announcer/03-clean-before-compress/` — the sanitize step:
  strips SSH host keys and machine-id (regenerated on first boot instead),
  clears bash history/apt cache/logs/tmp, zeroes free space. Never bakes the
  RAUC *signing* key into the image — only the public verification
  certificate goes into the image's keyring; the private key stays a CI
  secret.
- `rauc/` — `system.conf` (A/B rootfs slot classes + persistent `/data`
  slot, tryboot-based boot switching) and the cert/key setup used to
  produce signed `.raucb` bundles (`rauc bundle --cert=... --key=...`) from
  the raw pi-gen `.img`.
- `build.sh` — top-level, CI-invoked entrypoint.

See the main repo's `SLIDE_ANNOUNCER.md`, "Tier 1 — Base OS image" for the
full rationale (why pi-gen, why RAUC self-hosted over Mender, tryboot vs
U-Boot, the persistent-`/data` discipline, and the idle-window update-safety
policy shared with Tier 2).
