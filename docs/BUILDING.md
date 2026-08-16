# Building and flashing the image

This covers the current state of the pipeline: a bootable image with the
pi-gen/system/provisioning pieces for real, a real WiFi/network settings
menu (local-app), and pairing/slide-sync still not implemented — see
`../SLIDE_ANNOUNCER.md` and each directory's README for what's real vs.
stubbed.

## Prerequisites (build host)

- Docker (rootless or in the `docker` group — no `sudo docker` needed)
- `parted`, `dosfstools`, `e2fsprogs`, `rsync`, `xz-utils`, `rauc` — for
  `image-builder/repartition.sh` (needs `sudo`) and RAUC bundle signing
- Node.js/`npm` — builds `local-app/frontend`'s Vue app before staging
  (device itself never runs Node; only the built `dist/` goes on the image)
- ~10GB free disk space

## One-time setup: server URL + RAUC signing cert/key

`build.sh` checks all of this up front and exits cleanly with instructions
if anything's missing, before running the long pi-gen build.

**Server URL** — every device built from the image needs to know which
AnnouncementSlides server to pair/sync/check-updates against. One
self-hosted server per fleet, so this is a single build-time value, not a
per-device setting:

```bash
cd slideannouncer/image-builder
cp .env.example .env
# edit .env: SLIDE_ANNOUNCER_SERVER_URL=https://your-server.example.org
```

**WiFi regulatory domain** — optional, defaults to `US`. The Pi's WiFi
radio is soft rfkill-blocked by the kernel until a country is set (this is
true regardless of NetworkManager config — nothing on the device side can
work around it), so if devices deploy somewhere other than the US, set
this before building or Settings > Network's WiFi scan won't find
anything until someone runs `sudo raspi-config nonint do_wifi_country <CC>`
by hand at the console:

```bash
# edit .env: SLIDE_ANNOUNCER_WIFI_COUNTRY=US
```

**Extra config.txt lines** — optional, for hardware this fleet needs that a
stock image doesn't configure (a fan control overlay, for example):

```bash
# edit .env: SLIDE_ANNOUNCER_BOOT_CONFIG_EXTRA=dtoverlay=gpio-fan,gpiopin=14,temp=65000
```

Baked in under its own `[all]` section, so it applies unconditionally.
This survives RAUC OTA/tryboot switching without any special handling —
every future OTA bundle gets built from this same `.env` file too, so
whichever slot ends up active always carries it.

**RAUC signing cert/key** — the build also produces a signed `.raucb` OTA
bundle alongside the raw image, which needs a cert/key pair:

```bash
./generate-rauc-cert.sh dev   # throwaway self-signed pair, dev/test only
# edit .env with the paths it printed
```

A real fleet build should use `./generate-rauc-cert.sh production` instead
— it sets up an offline CA plus a rotatable signing cert issued by it, and
prints (and writes to a `MANIFEST.txt` alongside the generated files)
exactly what needs to be backed up, how sensitively, and what to do if a
key leaks. Read that before deciding where any of it ends up.

## Build

```bash
cd slideannouncer/image-builder
./build.sh
```

First run also clones the `pi-gen` submodule if it isn't already checked
out. Output — two artifacts, same version stamp:
- `image-builder/deploy/slideannouncer-<build-date>-<git-hash>.img.xz` —
  the full disk image for initial flashing (e.g.
  `slideannouncer-2026-08-06-a1b2c3d.img.xz`; `-dirty` appended to the hash
  if the repo had uncommitted changes at build time).
- `image-builder/deploy/slideannouncer-<build-date>-<git-hash>.raucb` — the
  same rootA content, signed as a RAUC bundle for OTA delivery. Actual
  on-device A/B activation is still a stub (see
  `image-builder/README.md`) — this covers building/signing/transferring
  bundles, not yet installing them.

The same `<kernel-version>-<build-date>-<git-hash>` string is baked into the
image at `/opt/slide-announcer/VERSION`, surfaced by the local backend's
`GET /api/local/status` (`image_version`) and the kiosk page, and reused
verbatim as the `.raucb`'s manifest `version=` — so you can tell which
build a running device is on without re-flashing to check, and the two
artifacts from one build always agree on version.

For a debug build with SSH also enabled (never for a real fleet image — the
default build has no SSH access at all):

```bash
SSH_DEV_BUILD=1 ./build.sh
```

Every build — dev or not — prints a random password for the `slideadmin`
local account to the console. That's a real local login (keyboard +
monitor), meant for field debugging, not deferred to Raspberry Pi OS's
interactive first-boot wizard (which would otherwise ask for a keyboard
layout and walk through account creation on every fresh card, fighting the
kiosk for the console). It's only reachable over SSH if `SSH_DEV_BUILD=1`
was used for that build.

## Flash

```bash
xzcat image-builder/deploy/slideannouncer-*.img.xz | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync
```

(or use `rpi-imager` → "Use custom image"). `/dev/sdX` is your SD
card/USB-SSD — double-check with `lsblk` first, this is destructive.

Before first boot, check `lsblk`/`fdisk -l /dev/sdX` shows all 4
partitions — `boot`, `rootA`, `rootB` (unused placeholder, same size as
`rootA`), and `data` (small placeholder, occupies the remaining space on
the card at this point, before it grows).

## Pre-provisioning WiFi + identity (optional, true headless)

Before first boot, you can drop a `slideannouncer.yaml` onto the boot
partition (mount it on any Mac/PC/Linux box — it's plain FAT32):

```yaml
wifi:
  ssid: "Church WiFi"
  password: "..."
```

Leave `device_uuid`/`device_uuid_check` out — `provisioning/firstboot.py`
generates a fresh, consistent pair on first boot. If you hand-edit
`device_uuid` later without the matching secret (which never leaves
`/data`), the next boot detects the mismatch and regenerates a fresh
identity — this is expected, not a bug (see
`../provisioning/README.md`).

## What to expect on first boot

1. Before `/data` is ever mounted, `slide-announcer-factory-reset-check.service`
   grows its partition to fill the card, then formats it — growing before
   formatting (not after) means `mke2fs` sizes block size/journal/inode
   density for the real final size directly, rather than for the tiny
   build-time placeholder. `slide-announcer-data-resize.service` still
   runs once `/data` is mounted as a safety net for the rare case the
   early grow didn't happen — normally a no-op by this point.
2. `slide-announcer-firstboot.service` regenerates SSH host keys/
   `machine-id` (once, ever), writes `/data/identity.key` and the
   `device_uuid`/`device_uuid_check` pair to the boot partition, and
   detects/records the setup mode (`headless-config` if you pre-provisioned
   WiFi above, `hid-setup` if a keyboard+pointer is attached, otherwise
   `ap-mode-fallback` — none of these are *acted on* yet, just detected).
3. `slide-announcer-local-app-seed.service` extracts the local-app release
   tarball baked into this image onto `/data/local-app/releases/<version>/`
   and points `/data/local-app/current` at it (first boot only ever seeds;
   it never has anything older to compare against yet) — see
   `../local-app/README.md`, "Installation on the device."
4. The kiosk display comes up (`labwc` + Chromium) showing the home page:
   hostname, image version, device UUID, and the detected setup mode, plus
   a "Settings" link into the real Network settings menu (WiFi scan/
   connect, connection status) — see `../local-app/README.md`. Pairing/
   slide sync are still not implemented.

If Settings > Network's WiFi scan reports no networks found, there are two
independent things to check — a device can fail either one separately:
- `rfkill list` at the console — `Soft blocked: yes` on the `wlan` line is
  the kernel-level regulatory-domain block; `sudo raspi-config nonint
  do_wifi_country US` (swap in the right code) fixes a running device
  without a rebuild.
- `nmcli radio wifi` — `disabled` means NetworkManager's own admin flag is
  off (a separate thing pi-gen's stock stage2 sets when it doesn't see a
  `WPA_COUNTRY` value — see `build.sh`'s comment where it sets that for
  pi-gen). `sudo nmcli radio wifi on` fixes a running device, but won't
  survive a reboot on its own (`/var` resets every boot) — that needs
  `image-builder/build.sh`'s own fix (forcing `WirelessEnabled=true` into
  `/var/lib/NetworkManager/NetworkManager.state` at build time) baked into
  the image via a rebuild+reflash.

Both are confirmed working on real hardware.

## Verifying the read-only rootfs

- `mount | grep ' / '` should show `ro` for the root filesystem.
- `touch /some-new-file` at the root of the filesystem should fail with
  "Read-only file system"; `touch /etc/some-new-file` and
  `touch /var/some-new-file` should both succeed (the overlays).
- `mount | grep overlay` should show two overlay mounts, on `/etc` and
  `/var`.
- Reboot, then check `/var/some-new-file` again — it should be gone
  (tmpfs-backed, resets every boot), while `/etc/some-new-file` should
  still be there (backed by `/data`).

## Verifying the anti-clone identity check

- `df -h /data` should show it grown to (close to) the full card size, not
  the small placeholder.
- `cat /boot/firmware/slideannouncer.yaml` (from another machine, or SSH on
  a dev build) should show a `device_uuid`/`device_uuid_check` pair.
- Hand-edit `device_uuid` in that file (leave `device_uuid_check`
  unchanged) and reboot — `journalctl -u slide-announcer-firstboot` should
  show "identity missing or inconsistent — regenerating," and the file
  should come back with a **new** `device_uuid`/`device_uuid_check` pair
  consistent with each other again.

## Testing the RAUC OTA pipeline (manual, no server needed)

There's no automatic update-check timer yet — `/usr/local/sbin/slide-announcer-update`
(source: `system/scripts/rauc-update.py`) is a CLI for exercising the
pieces that do exist, over SSH on a dev build (`SSH_DEV_BUILD=1`):

```bash
scp image-builder/deploy/slideannouncer-*.raucb slideadmin@<device-ip>:/tmp/
ssh slideadmin@<device-ip>
sudo slide-announcer-update install /tmp/slideannouncer-*.raucb
sudo slide-announcer-update status              # inspect before rebooting
sudo slide-announcer-update tryboot --yes       # reboots the device now
```

`install` writes both slots (rootfs into `rootB`, kernel/initramfs/config/
cmdline into `boot/firmware/slotB/`) and stages `tryboot.txt` via
`rpi-tryboot-backend.sh` — nothing switches yet at that point. `tryboot`
triggers the actual reboot; on the way back up,
`slide-announcer-tryboot-check.service` should detect the tryboot boot and
(today's placeholder health check: simply reaching that unit) commit slot
B as the new default and call `rauc status mark-good`. Check
`journalctl -u slide-announcer-tryboot-check` and
`sudo slide-announcer-update status` after it comes back to see whether
that happened. **This whole cycle is now confirmed working end-to-end on
real hardware** (0.1.10 → 0.2.1, 2026-08-13): install, tryboot reboot,
and commit (`os_prefix` flip + `mark-good`) all verified, and a
subsequent power cycle confirmed the committed slot stays the default —
see `system/rauc/rpi-tryboot-backend.sh` and `rpi-tryboot-commit.sh`'s
own headers for the specifics that were confirmed along the way. The
forced-bad-health → automatic-rollback path is still unverified, since
today's health check is just a placeholder ("we reached this unit").

A signature/hash failure at the `install` step instead (e.g. after
re-signing with a different cert, or corrupting the bundle) is what proves
verification itself is actually working, rather than being silently
skipped.

`slide-announcer-update check` calls the real heartbeat contract from
`SLIDE_ANNOUNCER.md`, Part 1 — but that server endpoint isn't implemented
yet, and no pairing flow exists to populate `/data/device-token`, so
`check` (and bare `install` with no argument, which calls `check`
internally) will fail with a clear "not paired" message until both of
those land. `install <path-or-url>` works standalone in the meantime.
