#!/bin/bash
# Grows the /data partition (partition 4 of the boot disk: boot, rootA,
# rootB, /data) to fill whatever's left on the physical SD card/SSD, then
# grows its ext4 filesystem to match.
#
# Runs at most once (marker file on /data itself). Must run before
# slide-announcer-firstboot.service, which assumes /data has real room.
#
# rootA is a FIXED size by design (see image-builder/repartition.sh) so it
# stays a known, reproducible size for the future RAUC A/B slot pair — only
# /data auto-expands. This replaces (not supplements) the stock
# raspberrypi-sys-mods root-partition auto-resize, which this image masks.
set -euo pipefail

MARKER=/data/.data-resized
[ -f "$MARKER" ] && exit 0

DATA_SRC="$(findmnt -no SOURCE /data)"
DISK="/dev/$(lsblk -no PKNAME "$DATA_SRC")"
PART_NUM="$(lsblk -no PARTN "$DATA_SRC")"

if [ -z "$DISK" ] || [ -z "$PART_NUM" ]; then
	echo "slide-announcer-data-resize: could not determine disk/partition for /data ($DATA_SRC), skipping" >&2
	exit 0
fi

if [ "$PART_NUM" != "4" ]; then
	echo "slide-announcer-data-resize: /data is partition ${PART_NUM}, not 4 as expected — refusing to touch partition table" >&2
	exit 1
fi

echo "slide-announcer-data-resize: growing partition ${PART_NUM} on ${DISK} to fill the disk"
GROWPART_OUT="$(growpart "$DISK" "$PART_NUM" 2>&1)" && echo "$GROWPART_OUT" || {
	rc=$?
	# growpart exits 1 with "NOCHANGE" if already at full size (e.g. re-run
	# after a crash between growpart and resize2fs) — treat as success.
	echo "$GROWPART_OUT"
	echo "$GROWPART_OUT" | grep -q NOCHANGE || exit "$rc"
}

echo "slide-announcer-data-resize: growing ext4 filesystem on ${DATA_SRC}"
resize2fs "$DATA_SRC"

touch "$MARKER"
echo "slide-announcer-data-resize: done"
