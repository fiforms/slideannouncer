# image-builder/

**Tier 1 — base OS image.** Produces the rarely-updated Raspberry Pi OS
image for a Raspberry Pi 4 (arm64, Bookworm).

**Implemented so far:** the pi-gen pipeline, custom stage, and partition
layout described below. **Not yet implemented:** RAUC itself (bundle
signing, `system.conf`, actual A/B switching, tryboot) — see
`SLIDE_ANNOUNCER.md` for why that's deliberately deferred. `rootB` exists in
the partition table as an empty, unbooted placeholder until that work
happens.

## Contents

- `pi-gen/` — official [pi-gen](https://github.com/RPi-Distro/pi-gen)
  builder, vendored as a git submodule pinned to the `bookworm-arm64`
  branch (arm64 specifically — the plain `bookworm` branch defaults to
  armhf).
- `config` — pi-gen config: arm64/Bookworm, Lite base (`stage0`-`stage2`,
  no X11 desktop — we bring our own Wayland compositor), plus our
  out-of-tree `stage-slide-announcer`.
- `stage-slide-announcer/` — the custom pi-gen stage (pi-gen only scans
  *sub-directories* of a stage for numbered steps, so everything lives one
  level down from the stage root — a bare file at the stage root is
  silently never run):
  - `prerun.sh` — seeds this stage's rootfs from stage2's finished one
    (every pi-gen stage needs this; it's not optional).
  - `01-system-files/00-packages` — Chromium, `labwc`, `seatd`,
    NetworkManager, nginx, Python, `evtest`/`libinput-tools`,
    `cloud-guest-utils` (`growpart`, needed on the device itself by
    `slide-announcer-data-resize.service`) — installed before `00-run.sh`
    below runs, per pi-gen's own per-prefix ordering.
  - `01-system-files/00-run.sh` — installs `system/`, `provisioning/`, and
    `local-app/` (staged into `files/` by `build.sh` before the pi-gen
    build starts, since pi-gen's Docker build context is just the `pi-gen/`
    directory — see `build.sh`'s comments) into the rootfs, creates the
    dedicated `slideannouncer` service user, enables the systemd units.
  - `02-clean-before-compress/` — sanitize step: strips SSH host keys and
    resets `machine-id` (regenerated for real by
    `provisioning/firstboot.py` on the device's actual first boot), clears
    apt cache/logs/tmp. Never touches a RAUC signing key — there isn't one
    yet, and even once there is, only the public verification cert belongs
    in the image.
- `repartition.sh` — post-processing pass over pi-gen's raw (boot + root)
  `.img` output. pi-gen only ever produces a 2-partition image sized
  tightly to content; this repartitions it into the 4-partition layout
  this project settles on ahead of RAUC:

  ```
  p1  boot   FAT32  unchanged, copied as-is
  p2  rootA  ext4   fixed size (default 3GiB), pi-gen's root content, ACTIVE
  p3  rootB  ext4   same fixed size, empty — unused future OTA slot
  p4  data   ext4   small placeholder; grown to fill the real SD card by
                     slide-announcer-data-resize.service on first boot
  ```

  Building this out now (rather than a single auto-expanding rootfs) means
  devices flashed today need no re-partitioning/data-migration once RAUC
  ships — just a normal OTA into the already-present `rootB`.
- `build.sh` — top-level entrypoint. See "Building," below.

## Building

Requires Docker (rootless or in the `docker` group) and, on the host, a
static aarch64 qemu interpreter registered for binfmt_misc (`qemu-user-static`
on Debian, `qemu-user-binfmt` on newer Ubuntu — `build.sh` auto-symlinks it
to the filename pi-gen's `build-docker.sh` expects if your distro dropped
the `-static` suffix, no sudo needed) plus
`parted`/`dosfstools`/`e2fsprogs`/`rsync`/`xz-utils` for the repartition
step (root privileges required there — `build.sh` calls `repartition.sh`
via `sudo`).

```bash
cd image-builder
./build.sh
# ... or, for a debug build with SSH enabled and a random password printed
# to the console (never for a real fleet image):
SSH_DEV_BUILD=1 ./build.sh
```

Output: `image-builder/deploy/slideannouncer-<build-date>-<git-hash>.img.xz`.
The same version string (plus the kernel version baked into the image) is
written to `/opt/slide-announcer/VERSION` and surfaced by the stub app, so a
running device can be identified without re-flashing. Flash with
`rpi-imager` or `xzcat ... | dd ...`. See `../docs/BUILDING.md` for the full
flash/first-boot walkthrough.

See the main repo's `SLIDE_ANNOUNCER.md`, "Tier 1 — Base OS image" for the
full rationale (why pi-gen, why RAUC self-hosted over Mender, tryboot vs
U-Boot, the persistent-`/data` discipline, and the idle-window update-safety
policy shared with Tier 2).
