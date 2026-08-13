# image-builder/

**Tier 1 — base OS image.** Produces the rarely-updated Raspberry Pi OS
image for a Raspberry Pi 4 (arm64, Trixie).

**Implemented so far:** the pi-gen pipeline, custom stage, partition layout
described below, RAUC bundle production (`system.conf`, signed `.raucb`
output covering both rootfs and boot-partition kernel/initramfs content),
a surgical hotfix mechanism (`make-hotfix-bundle.sh`, `hotfixes/`) for
patching a running rootfs in place without an A/B swap, and a full attempt
at A/B slot switching via Raspberry Pi tryboot
(`system/rauc/rpi-tryboot-backend.sh` + `rpi-tryboot-commit.sh`,
`slide-announcer-update tryboot`).

The hotfix mechanism is **confirmed working end-to-end on real hardware**
(2026-08-10): `rauc install` of a hotfix `.raucb` over HTTP, version-gate
check, live-rootfs bind-mount write-through, and `/opt/slide-announcer/VERSION`
bump all verified on an actual device.

The tryboot path is now also **confirmed working end-to-end on real
hardware** (2026-08-11): `rauc install` staging the inactive slot,
`tryboot.txt`/`os_prefix` (a full `config.txt` copy with only `os_prefix`
swapped), the `/proc/device-tree/chosen/bootloader/tryboot` boot-detection
flag (a 4-byte big-endian devicetree cell, not text), and the commit step
(`config.txt` flip + `rauc status mark-good` + normal reboot) all verified
on an actual device — see each script's own header for what testing
surfaced and fixed along the way. Reconfirmed 2026-08-13 with a real field
OTA on a paired production device (0.1.10 → 0.2.1): device came up on
0.2.0 and resumed slide sync/display, and a subsequent power cycle stayed
on 0.2.0, confirming the commit persists across a normal reboot.

**Still unverified:** the forced-bad-health → rollback path. The
post-tryboot health check `rpi-tryboot-commit.sh` runs is currently just a
placeholder ("we reached this unit"), so every tryboot so far has taken the
commit branch — no real failure has ever been forced through it. See
`SLIDE_ANNOUNCER.md`'s open questions.

## Contents

- `pi-gen/` — official [pi-gen](https://github.com/RPi-Distro/pi-gen)
  builder, vendored as a git submodule pinned to the `trixie-arm64`
  branch (arm64 specifically — the plain `trixie` branch defaults to
  armhf).
- `config` — pi-gen config: arm64/Trixie, Lite base (`stage0`-`stage2`,
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
    Also wires up the read-only rootfs: rewrites `/etc/fstab`'s root entry
    and `cmdline.txt` to mount `ro`, appends the `/tmp`+`/var/tmp` tmpfs
    and `/etc`+`/var` overlay fstab entries, and installs
    `system/read-only-root/`'s tmpfiles/journald config (see
    `system/README.md` and `SLIDE_ANNOUNCER.md`, Tier 1, "Read-only
    rootfs").
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
  p1  boot   FAT32  kernel/initramfs/config.txt/cmdline.txt at the root
                     (whichever slot is currently committed) plus
                     slotA/ and slotB/ subdirs (RAUC's "kernel" custom
                     slot target — see system/rauc/system.conf)
  p2  rootA  ext4   fixed size (default 3GiB) reserved in the partition
                     table, pi-gen's root content copied in then shrunk to
                     that content's actual size, ACTIVE
  p3  rootB  ext4   same fixed size reserved, no filesystem written at all
                     — unused until first OTA
  p4  data   ext4   small placeholder size reserved, no filesystem written
                     at all — formatted on first boot (see below), then
                     grown to fill the real SD card by
                     slide-announcer-data-resize.service
  ```

  The output `.img` file is truncated right after rootA's shrunk
  filesystem — rootB and `data` are only partition-table entries at that
  point, with no bytes of their own in the file, so flashing doesn't spend
  time writing several gigabytes of zeros for partitions nothing reads
  before first boot/first OTA. rootA is mounted read-only on the device, so
  it never needs to grow back to fill its reserved partition size the way
  `data` does.

  Because `data` has no filesystem in the shipped image, this script also
  drops a `FACTORY_RESET` flag onto the boot partition so that
  `slide-announcer-factory-reset-check.service` always formats it fresh on
  the very first boot — the same path a manual factory reset already used,
  now doing double duty. Even `/etc`'s read-only-rootfs overlay upper/work
  directories (which have to exist before the very first boot's overlay
  mount) are created at boot instead (`slide-announcer-data-dirs.service`),
  not seeded here, so a brand-new card and a post-factory-reset `/data` go
  through the exact same boot-time path rather than two mechanisms that
  could drift apart. Also seeds `boot/firmware/slotA/` to mirror the
  partition root (this build's active slot) while `slotB/` starts empty.
  RAUC's own A/B bookkeeping needs nothing pre-seeded on `/data` at all —
  see `system/rauc/rpi-tryboot-backend.sh` for why (it's derived from the
  running kernel's `/proc/cmdline` instead, specifically so a `/data`
  wipe/factory-reset can't desync it).

  Building this out now (rather than a single auto-expanding rootfs) means
  devices flashed today need no re-partitioning/data-migration for a RAUC
  OTA — just a normal install into the already-present `rootB`/`slotB/`.
  The partitions are reserved at a fixed size larger than any current
  content specifically so a future, larger rootfs still fits without a
  layout change.
- `build.sh` — top-level entrypoint. See "Building," below.
- `generate-rauc-cert.sh` — generates RAUC bundle-signing cert/key
  material (`image-builder/certs/`, gitignored), in two modes: `dev` (a
  throwaway single self-signed pair) or `production` (an offline CA plus
  a rotatable signing cert issued by it — see its own header comment and
  the `MANIFEST.txt` it writes for what to back up and how).
- `.env.example` — copy to `.env` (gitignored) and set `SLIDE_ANNOUNCER_SERVER_URL`
  (the fleet's AnnouncementSlides server, baked into every image at
  `/etc/slide-announcer/server-url`) plus `RAUC_CERT_PATH`/`RAUC_KEY_PATH`
  (and `RAUC_KEYRING_CERT_PATH` for a production PKI) — `build.sh` refuses
  to build without all of these set. `SLIDE_ANNOUNCER_WIFI_COUNTRY` is
  optional (defaults to `US`) — the WiFi radio ships soft rfkill-blocked
  by the kernel until a regulatory domain is set, regardless of
  NetworkManager config, so this needs to be right for wherever devices
  actually deploy or Settings > Network's WiFi scan won't see anything.
  `SLIDE_ANNOUNCER_BOOT_CONFIG_EXTRA` is also optional — free-form extra
  `config.txt` lines for hardware this fleet needs (a fan control overlay,
  etc.), appended under their own `[all]` section by `00-run.sh`. Survives
  RAUC OTA/tryboot switching, since every future OTA bundle is built from
  this same `.env` too.

## Building

Requires Docker (rootless or in the `docker` group) and, on the host, a
static aarch64 qemu interpreter registered for binfmt_misc (`qemu-user-static`
on Debian, `qemu-user-binfmt` on newer Ubuntu — `build.sh` auto-symlinks it
to the filename pi-gen's `build-docker.sh` expects if your distro dropped
the `-static` suffix, no sudo needed) plus
`parted`/`dosfstools`/`e2fsprogs`/`rsync`/`xz-utils`/`rauc` for the
repartition and bundle-signing steps (root privileges required for
repartitioning — `build.sh` calls `repartition.sh` via `sudo`).

First, `.env` (skip if you already have one set up):

```bash
cd image-builder
cp .env.example .env
# edit .env: SLIDE_ANNOUNCER_SERVER_URL=https://your-server.example.org

./generate-rauc-cert.sh dev     # dev/test only — prints paths to use below
# ... or for a real fleet:
./generate-rauc-cert.sh production   # see its MANIFEST.txt for backup instructions
# edit .env with the RAUC_* paths it printed
```

```bash
./build.sh
# ... or, with SSH also enabled (never for a real fleet image):
SSH_DEV_BUILD=1 ./build.sh
```

Every build (dev or not) prints a random password for the `slideadmin`
local account to the console — a real local login for physical-keyboard
field debugging, not deferred to Raspberry Pi OS's interactive first-boot
wizard (which would otherwise contest the console with the kiosk on every
fresh card). Not exposed remotely unless `SSH_DEV_BUILD=1` was used.

For a real fleet image that still needs remote SSH access, set both
`SLIDE_ANNOUNCER_ENABLE_SSH=1` and `SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH` in
`.env` instead of `SSH_DEV_BUILD` — that loads the given public key into
`slideadmin`'s `~/.ssh/authorized_keys` and disables SSH password
authentication globally, so key-based login is the only way in. Leaving
either one unset keeps SSH entirely disabled.

Output — two artifacts, same version stamp:
- `image-builder/deploy/slideannouncer-<build-date>-<git-hash>.img.xz` — the
  full boot+rootA+rootB+data disk, for initial flashing only.
- `image-builder/deploy/slideannouncer-<build-date>-<git-hash>.raucb` — the
  same rootA content, packaged and signed as a RAUC bundle for OTA
  (`rauc install <url>` on a device; actual A/B activation is still a stub —
  see "Not yet implemented," above).

The same version string (plus the kernel version baked into the image) is
written to `/opt/slide-announcer/VERSION` and surfaced by the stub app, so a
running device can be identified without re-flashing — the `.raucb`'s
manifest `version=` is read back out of that same file, so both artifacts
always agree. Flash the `.img.xz` with `rpi-imager` or `xzcat ... | dd ...`.
See `../docs/BUILDING.md` for the full flash/first-boot walkthrough.

See the main repo's `SLIDE_ANNOUNCER.md`, "Tier 1 — Base OS image" for the
full rationale (why pi-gen, why RAUC self-hosted over Mender, tryboot vs
U-Boot, the persistent-`/data` discipline, and the idle-window update-safety
policy shared with Tier 2).
