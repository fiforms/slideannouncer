# image-builder/

**Tier 1 — base OS image.** Produces the rarely-updated Raspberry Pi OS
image for a Raspberry Pi 4 (arm64, Trixie).

**Implemented so far:** the pi-gen pipeline, custom stage, partition layout
described below, RAUC bundle production (`system.conf`, signed `.raucb`
output covering both rootfs and boot-partition kernel/initramfs content),
and a full attempt at A/B slot switching via Raspberry Pi tryboot
(`system/rauc/rpi-tryboot-backend.sh` + `rpi-tryboot-commit.sh`,
`slide-announcer-update tryboot`). **HARDWARE-UNVERIFIED:** none of the
tryboot-specific pieces (the `tryboot.txt`/`os_prefix` mechanism, the
`/proc/device-tree/chosen/bootloader/tryboot` boot-detection flag, RAUC's
exact custom-bootloader-backend and custom-slot-hook argv/env contracts)
have been run through a real install → tryboot reboot → forced-bad-health
→ rollback cycle on actual hardware — see each script's own header and
`SLIDE_ANNOUNCER.md`'s open questions. Treat this as a real first attempt
to validate against real hardware, not a proven implementation.

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
  p2  rootA  ext4   fixed size (default 3GiB), pi-gen's root content, ACTIVE
  p3  rootB  ext4   same fixed size, empty — unused until first OTA
  p4  data   ext4   small placeholder; grown to fill the real SD card by
                     slide-announcer-data-resize.service on first boot
  ```

  `data` gets a filesystem here but is otherwise left empty — even `/etc`'s
  read-only-rootfs overlay upper/work directories (which have to exist
  before the very first boot's overlay mount) are created at boot instead
  (`slide-announcer-data-dirs.service`), not seeded here, so a brand-new
  card and a post-factory-reset `/data` go through the exact same
  boot-time path rather than two mechanisms that could drift apart. Also
  seeds `boot/firmware/slotA/` to mirror the partition root (this build's
  active slot) while `slotB/` starts empty. RAUC's own A/B bookkeeping needs
  nothing pre-seeded on `/data` at all — see `system/rauc/rpi-tryboot-backend.sh`
  for why (it's derived from the running kernel's `/proc/cmdline`
  instead, specifically so a `/data` wipe/factory-reset can't desync it).

  Building this out now (rather than a single auto-expanding rootfs) means
  devices flashed today need no re-partitioning/data-migration for a RAUC
  OTA — just a normal install into the already-present `rootB`/`slotB/`.
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
