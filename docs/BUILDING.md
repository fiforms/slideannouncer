# Building and flashing the image

This covers the current state of the pipeline: a bootable image with the
pi-gen/system/provisioning pieces for real, and a **stub** local-app just
proving the boot → firstboot → kiosk pipeline works end-to-end. No
WiFi/pairing/sync yet — see `../SLIDE_ANNOUNCER.md` and each directory's
README for what's real vs. stubbed.

## Prerequisites (build host)

- Docker (rootless or in the `docker` group — no `sudo docker` needed)
- `parted`, `dosfstools`, `e2fsprogs`, `rsync`, `xz-utils` — for
  `image-builder/repartition.sh`, which needs `sudo`
- ~10GB free disk space

## Build

```bash
cd slideannouncer/image-builder
./build.sh
```

First run also clones the `pi-gen` submodule if it isn't already checked
out. Output: `image-builder/deploy/slideannouncer-<build-date>-<git-hash>.img.xz`
(e.g. `slideannouncer-2026-08-06-a1b2c3d.img.xz`; `-dirty` appended to the
hash if the repo had uncommitted changes at build time). The same
`<kernel-version>-<build-date>-<git-hash>` string is baked into the image
at `/opt/slide-announcer/VERSION` and surfaced by the stub's
`GET /api/local/status` (`image_version`) and the kiosk page — so you can
tell which build a running device is on without re-flashing to check.

For a debug build with SSH enabled (never for a real fleet image — the
default build has no SSH access at all):

```bash
SSH_DEV_BUILD=1 ./build.sh
```

The console prints a randomly generated password for the `slideadmin` user.

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

1. `slide-announcer-data-resize.service` grows `/data` to fill the card.
2. `slide-announcer-firstboot.service` regenerates SSH host keys/
   `machine-id` (once, ever), writes `/data/identity.key` and the
   `device_uuid`/`device_uuid_check` pair to the boot partition, and
   detects/records the setup mode (`headless-config` if you pre-provisioned
   WiFi above, `hid-setup` if a keyboard+pointer is attached, otherwise
   `ap-mode-fallback` — none of these are *acted on* yet, just detected).
3. The kiosk display comes up (`labwc` + Chromium) showing the stub page:
   hostname, image version, device UUID, and the detected setup mode. This
   is the end-to-end proof the image works — no WiFi/pairing yet.

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
