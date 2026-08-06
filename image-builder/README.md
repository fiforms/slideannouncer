# image-builder/

**Tier 1 — base OS image.** Produces the rarely-updated Raspberry Pi OS
image and packages it as a Mender-compatible A/B OTA artifact.

Planned contents (not yet implemented):
- `pi-gen/` — pinned submodule/checkout of the official [pi-gen](https://github.com/RPi-Distro/pi-gen) builder.
- `stage-slide-announcer/` — custom pi-gen stage injecting Chromium, a
  minimal compositor (labwc), NetworkManager, nginx, the Mender client, and
  this repo's `system/` units/polkit rules.
- `stage-slide-announcer/03-clean-before-compress/` — the sanitize step:
  strips SSH host keys and machine-id (regenerated on first boot instead),
  clears bash history/apt cache/logs/tmp, zeroes free space. Never bakes OTA
  *signing* keys into the image — only public verification keys.
- `mender-artifact/` — `mender-convert` config to post-process the raw
  pi-gen `.img` into a `.mender` A/B-partitioned artifact (rootfs_a/rootfs_b
  + a persistent `/data` partition).
- `build.sh` — top-level, CI-invoked entrypoint.

See the main repo's `SLIDE_ANNOUNCER.md`, "Tier 1 — Base OS image" for the
full rationale (why pi-gen, why Mender over RAUC, the persistent-`/data`
discipline, and the scheduled-rollout update-safety policy).
