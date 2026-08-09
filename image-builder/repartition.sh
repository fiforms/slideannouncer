#!/bin/bash
# Takes pi-gen's raw (boot + root) .img output and repartitions it into the
# 4-partition layout this project's design settles on ahead of RAUC itself
# (see SLIDE_ANNOUNCER.md and image-builder/README.md):
#
#   p1  boot   FAT32  unchanged, copied as-is from pi-gen's output
#   p2  rootA  ext4   fixed size (reserved in the partition table for future
#                      RAUC bundles), pi-gen's root content copied in then
#                      shrunk to that content's actual size, ACTIVE
#   p3  rootB  ext4   same fixed size reserved, but no filesystem is written
#                      here at all — unused until the first OTA install
#   p4  data   ext4   same story: partition table entry only, no filesystem.
#                      slide-announcer-factory-reset-check.service formats it
#                      fresh on the device's first boot (this script drops a
#                      FACTORY_RESET flag onto the boot partition so that
#                      path always runs once), then
#                      slide-announcer-data-resize.service grows it to fill
#                      the real SD card (can't be sized correctly inside the
#                      .img file itself — "rest of the card" is only known
#                      once it's on real hardware).
#
# The output .img file is truncated right after rootA's (shrunk) filesystem
# — rootB and data have no bytes in the file at all, only partition-table
# entries reserving their space, so flashing this image doesn't spend time
# writing multiple gigabytes of zeros for partitions nothing reads before
# the device's first boot or first OTA install.
#
# Must run as root (loop devices, mount). Not a pi-gen stage — pi-gen only
# ever produces a 2-partition image sized tightly to content, so this is a
# separate post-processing pass over its finished output.
set -euo pipefail

SRC_IMG="${1:?usage: repartition.sh <src.img> <out.img>}"
OUT_IMG="${2:?usage: repartition.sh <src.img> <out.img>}"

ROOTFS_FIXED_SIZE_MB="${ROOTFS_FIXED_SIZE_MB:-5120}"
DATA_PLACEHOLDER_SIZE_MB="${DATA_PLACEHOLDER_SIZE_MB:-128}"
ALIGN=$((8 * 1024 * 1024))

if [ "$(id -u)" != "0" ]; then
	echo "repartition.sh must run as root (loop devices, mount)" >&2
	exit 1
fi

WORK_DIR="$(mktemp -d)"
SRC_LOOP=""
DST_LOOP=""
SRC_BOOT_MNT="${WORK_DIR}/src-boot"
SRC_ROOT_MNT="${WORK_DIR}/src-root"
DST_ROOT_MNT="${WORK_DIR}/dst-root"
mkdir -p "$SRC_BOOT_MNT" "$SRC_ROOT_MNT" "$DST_ROOT_MNT"

cleanup() {
	set +e
	umount "${DST_ROOT_MNT}/boot/firmware" 2>/dev/null
	umount "$DST_ROOT_MNT" 2>/dev/null
	umount "$SRC_BOOT_MNT" 2>/dev/null
	umount "$SRC_ROOT_MNT" 2>/dev/null
	[ -n "$DST_LOOP" ] && losetup -d "$DST_LOOP" 2>/dev/null
	[ -n "$SRC_LOOP" ] && losetup -d "$SRC_LOOP" 2>/dev/null
	rm -rf "$WORK_DIR"
}
trap cleanup EXIT

align_up() {
	local n=$1
	echo $(( (n + ALIGN - 1) / ALIGN * ALIGN ))
}

# --- read pi-gen's boot/root partition geometry -----------------------------
part_info="$(parted -ms "$SRC_IMG" unit B print)"
boot_line="$(echo "$part_info" | awk -F: '$1 == "1"')"
root_line="$(echo "$part_info" | awk -F: '$1 == "2"')"

BOOT_START="$(echo "$boot_line" | cut -d: -f2 | tr -d B)"
BOOT_SIZE="$(echo "$boot_line" | cut -d: -f4 | tr -d B)"
ROOT_SIZE_ACTUAL="$(echo "$root_line" | cut -d: -f4 | tr -d B)"

FIXED_ROOT_SIZE_BYTES=$((ROOTFS_FIXED_SIZE_MB * 1024 * 1024))
if [ "$ROOT_SIZE_ACTUAL" -gt "$FIXED_ROOT_SIZE_BYTES" ]; then
	echo "repartition.sh: pi-gen's root content (${ROOT_SIZE_ACTUAL}B) exceeds" \
		"ROOTFS_FIXED_SIZE_MB=${ROOTFS_FIXED_SIZE_MB}MB — using the larger, actual size instead" >&2
	FIXED_ROOT_SIZE_BYTES=$(align_up "$ROOT_SIZE_ACTUAL")
else
	FIXED_ROOT_SIZE_BYTES=$(align_up "$FIXED_ROOT_SIZE_BYTES")
fi

ROOTA_START=$((BOOT_START + BOOT_SIZE))
ROOTB_START=$((ROOTA_START + FIXED_ROOT_SIZE_BYTES))
DATA_START=$((ROOTB_START + FIXED_ROOT_SIZE_BYTES))
DATA_SIZE_BYTES=$(align_up $((DATA_PLACEHOLDER_SIZE_MB * 1024 * 1024)))
NEW_IMG_SIZE=$((DATA_START + DATA_SIZE_BYTES))

echo "repartition.sh: boot=${BOOT_SIZE}B rootA/rootB=${FIXED_ROOT_SIZE_BYTES}B" \
	"data(placeholder)=${DATA_SIZE_BYTES}B total=${NEW_IMG_SIZE}B"

# --- build the new 4-partition image ----------------------------------------
truncate -s "$NEW_IMG_SIZE" "$OUT_IMG"
parted --script "$OUT_IMG" mklabel msdos
parted --script "$OUT_IMG" unit B mkpart primary fat32 "$BOOT_START" "$((ROOTA_START - 1))"
parted --script "$OUT_IMG" unit B mkpart primary ext4 "$ROOTA_START" "$((ROOTB_START - 1))"
parted --script "$OUT_IMG" unit B mkpart primary ext4 "$ROOTB_START" "$((DATA_START - 1))"
parted --script "$OUT_IMG" unit B mkpart primary ext4 "$DATA_START" "$((NEW_IMG_SIZE - 1))"

DST_LOOP="$(losetup --show --find --partscan "$OUT_IMG")"
SRC_LOOP="$(losetup --show --find --partscan --read-only "$SRC_IMG")"
udevadm settle 2>/dev/null || true

mkdosfs -n bootfs -F 32 -s 4 "${DST_LOOP}p1" > /dev/null
mkfs.ext4 -q -F -L rootA "${DST_LOOP}p2"
# p3 (rootB) and p4 (data) are deliberately left unformatted here — the
# image file gets truncated right after rootA below, so any filesystem
# written to them now would just be discarded anyway. rootB gets a real
# filesystem for the first time via its first OTA install; data gets one
# via slide-announcer-factory-reset-check.service on the device's first
# boot (see the FACTORY_RESET flag dropped onto the boot partition below).
# slide-announcer-data-dirs.service creates /data/overlay/etc/{upper,work}
# (needed before /etc's overlay can mount) on every boot, including right
# after that first-boot reformat, so there's no separate build-time seed to
# keep in sync with that runtime path — one mechanism covers "brand new
# card" and "post-reset" alike.

mount "${SRC_LOOP}p1" "$SRC_BOOT_MNT" -t vfat -o ro
mount "${SRC_LOOP}p2" "$SRC_ROOT_MNT" -t ext4 -o ro
mount "${DST_LOOP}p2" "$DST_ROOT_MNT" -t ext4

rsync -aHAXx --exclude /boot/firmware "${SRC_ROOT_MNT}/" "${DST_ROOT_MNT}/"

# Mount the new boot partition AT the nested path we're about to rsync into
# (matching how it's actually used on a running system, and how pi-gen
# itself does this) — mounting it at some other, unrelated temp dir instead
# would leave the real FAT32 partition empty while the "copied" firmware
# files silently land inside rootA's ext4 filesystem instead.
mkdir -p "${DST_ROOT_MNT}/boot/firmware"
mount "${DST_LOOP}p1" "${DST_ROOT_MNT}/boot/firmware" -t vfat
rsync -rtx "${SRC_BOOT_MNT}/" "${DST_ROOT_MNT}/boot/firmware/"

# --- rewrite PARTUUIDs for the new disk (pi-gen already baked in its own,
# now-stale, PARTUUIDs for its 2-partition layout) ---------------------------
NEW_DISK_ID="$(dd if="$OUT_IMG" skip=440 bs=1 count=4 2>/dev/null | xxd -e | cut -f2 -d' ')"
NEW_BOOT_PARTUUID="${NEW_DISK_ID}-01"
NEW_DATA_PARTUUID="${NEW_DISK_ID}-04"

sed -i -E "s/PARTUUID=[0-9a-fA-F]{8}-01/PARTUUID=${NEW_BOOT_PARTUUID}/" \
	"${DST_ROOT_MNT}/etc/fstab" "${DST_ROOT_MNT}/boot/firmware/cmdline.txt"
sed -i "s/DATADEV/PARTUUID=${NEW_DATA_PARTUUID}/" "${DST_ROOT_MNT}/etc/fstab"

# root= uses the ext4 LABEL, not a PARTUUID, unlike boot/data above — a
# PARTUUID is unique per built image (NEW_DISK_ID, randomly assigned right
# here), but a RAUC bundle is built once and installed fleet-wide into
# whichever of rootA/rootB is currently inactive on each individual
# device, so it can't carry a literal PARTUUID for "the slot it lands in."
# LABEL=rootA/rootB is a fleet-wide constant instead (set by mkfs.ext4
# above on every device), which is what makes image-builder/build.sh's
# bundled boot files portable across the fleet at all — see its
# bootfiles.tar.gz/hook.sh comments.
sed -i -E "s/PARTUUID=[0-9a-fA-F]{8}-02/LABEL=rootA/" \
	"${DST_ROOT_MNT}/etc/fstab" "${DST_ROOT_MNT}/boot/firmware/cmdline.txt"

# --- seed the boot partition's per-slot kernel/initramfs/cmdline/config
# directories (RAUC's "kernel" custom slot class — see
# system/rauc/system.conf and rpi-tryboot-backend.sh): slotA/ mirrors
# what's already at the partition root (this build's active slot); slotB/
# starts empty, same as rootB, until the first real OTA populates it.
BOOTFW="${DST_ROOT_MNT}/boot/firmware"
mkdir -p "${BOOTFW}/slotA" "${BOOTFW}/slotB"
rsync -rt --exclude /slotA --exclude /slotB --exclude /tryboot.txt \
	"${BOOTFW}/" "${BOOTFW}/slotA/"

# Every fresh build ships with this set, so slide-announcer-factory-reset-check.service
# always formats /data on the device's first boot — see the comment on the
# mkfs.ext4 calls above for why that's now the *only* place a data
# filesystem gets created (repartition.sh no longer writes one at build
# time). The service removes the flag itself once it's run.
touch "${BOOTFW}/FACTORY_RESET"

# --- shrink rootA to its actual content, then truncate the image file right
# after it. rootA is mounted read-only on the device (see
# stage-slide-announcer/01-system-files/00-run.sh), so it never needs to
# grow back to fill its (fixed, future-proofed) partition-table size —
# unlike /data, there's no first-boot step undoing this. rootB and data's
# partition-table entries still reserve their full fixed sizes, they just
# have no bytes in the file past this point.
umount "${DST_ROOT_MNT}/boot/firmware"
umount "$DST_ROOT_MNT"
umount "$SRC_BOOT_MNT"
umount "$SRC_ROOT_MNT"

e2fsck -fy "${DST_LOOP}p2" || true
resize2fs -M "${DST_LOOP}p2"
ROOTA_FS_BYTES="$(dumpe2fs -h "${DST_LOOP}p2" 2>/dev/null | awk -F: '
	/Block count/ { blocks = $2 }
	/Block size/  { size = $2 }
	END { print blocks * size }
')"

losetup -d "$DST_LOOP"
losetup -d "$SRC_LOOP"
DST_LOOP=""
SRC_LOOP=""

TRUNCATED_SIZE=$((ROOTA_START + ROOTA_FS_BYTES))
truncate -s "$TRUNCATED_SIZE" "$OUT_IMG"

echo "repartition.sh: wrote ${OUT_IMG} (truncated to ${TRUNCATED_SIZE}B after rootA's shrunk filesystem)"
echo "  boot  PARTUUID=${NEW_BOOT_PARTUUID}"
echo "  rootA LABEL=rootA  (active, shrunk to ${ROOTA_FS_BYTES}B content; partition table reserves $((FIXED_ROOT_SIZE_BYTES / 1024 / 1024))MiB; boot/firmware/slotA/ seeded to match)"
echo "  rootB (partition table reserves $((FIXED_ROOT_SIZE_BYTES / 1024 / 1024))MiB, no filesystem yet; boot/firmware/slotB/ empty until first OTA)"
echo "  data  PARTUUID=${NEW_DATA_PARTUUID}  (partition table reserves ${DATA_PLACEHOLDER_SIZE_MB}MiB, formatted by FACTORY_RESET flag on first boot, then grows)"
