#!/bin/bash
# Takes pi-gen's raw (boot + root) .img output and repartitions it into the
# 4-partition layout this project's design settles on ahead of RAUC itself
# (see SLIDE_ANNOUNCER.md and image-builder/README.md):
#
#   p1  boot   FAT32  unchanged, copied as-is from pi-gen's output
#   p2  rootA  ext4   fixed size, pi-gen's root content copied in, ACTIVE
#   p3  rootB  ext4   same fixed size, empty — unused OTA placeholder
#   p4  data   ext4   small placeholder here; grown to fill the real SD
#                      card by slide-announcer-data-resize.service on the
#                      device's first boot (can't be sized correctly inside
#                      the .img file itself — "rest of the card" is only
#                      known once it's on real hardware).
#
# Must run as root (loop devices, mount). Not a pi-gen stage — pi-gen only
# ever produces a 2-partition image sized tightly to content, so this is a
# separate post-processing pass over its finished output.
set -euo pipefail

SRC_IMG="${1:?usage: repartition.sh <src.img> <out.img>}"
OUT_IMG="${2:?usage: repartition.sh <src.img> <out.img>}"

ROOTFS_FIXED_SIZE_MB="${ROOTFS_FIXED_SIZE_MB:-3072}"
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
mkfs.ext4 -q -F -L rootB "${DST_LOOP}p3"
mkfs.ext4 -q -F -L data "${DST_LOOP}p4"

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
NEW_ROOTA_PARTUUID="${NEW_DISK_ID}-02"
NEW_DATA_PARTUUID="${NEW_DISK_ID}-04"

sed -i -E "s/PARTUUID=[0-9a-fA-F]{8}-01/PARTUUID=${NEW_BOOT_PARTUUID}/" \
	"${DST_ROOT_MNT}/etc/fstab" "${DST_ROOT_MNT}/boot/firmware/cmdline.txt"
sed -i -E "s/PARTUUID=[0-9a-fA-F]{8}-02/PARTUUID=${NEW_ROOTA_PARTUUID}/" \
	"${DST_ROOT_MNT}/etc/fstab" "${DST_ROOT_MNT}/boot/firmware/cmdline.txt"
sed -i "s/DATADEV/PARTUUID=${NEW_DATA_PARTUUID}/" "${DST_ROOT_MNT}/etc/fstab"

echo "repartition.sh: wrote ${OUT_IMG}"
echo "  boot  PARTUUID=${NEW_BOOT_PARTUUID}"
echo "  rootA PARTUUID=${NEW_ROOTA_PARTUUID}  (active, $((FIXED_ROOT_SIZE_BYTES / 1024 / 1024))MiB)"
echo "  rootB (unused placeholder, same size)"
echo "  data  PARTUUID=${NEW_DATA_PARTUUID}  (${DATA_PLACEHOLDER_SIZE_MB}MiB placeholder, grows on first boot)"
