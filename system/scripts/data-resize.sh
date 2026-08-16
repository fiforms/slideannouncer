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
#
# --partition-only DEVICE: grows the partition table entry only, skipping
# the ext4 resize and marker file — called by factory-reset-check.sh
# BEFORE its own mkfs.ext4, so mkfs sees the partition's true final size
# up front and picks block size/journal/inode density for that size
# directly, rather than mke2fs sizing everything for the tiny 128MiB
# placeholder and resize2fs being unable to fix the block size
# afterward (it can grow a filesystem, never change its block size).
# growpart only ever touches the partition table, never the filesystem
# inside it — confirmed safe to run here since there's no filesystem yet
# for it to have an opinion about. DEVICE is passed in explicitly rather
# than resolved from a live mount, since /data isn't mounted yet at this
# point — factory-reset-check.sh already resolved it from fstab for its
# own mkfs call, so this reuses that rather than re-deriving it.
# slide-announcer-data-resize.service's own (post-mount, full) run still
# happens afterward regardless: growpart finds nothing left to do
# (NOCHANGE), resize2fs is a no-op against an already-correctly-sized
# filesystem, and the marker gets written then, same as if this early
# call had never run — this is a pure head start, not a replacement.
set -euo pipefail

# growpart defaults its own scratch dir to ${TMPDIR:-/tmp} — this unit has
# DefaultDependencies=no (see its own comment) and isn't guaranteed to run
# after tmp.mount, so /tmp could still be the plain (read-only) root
# filesystem directory at this point rather than the real tmpfs. /run is
# always a tmpfs by the time any unit runs at all, set up by systemd
# itself before ordering begins — pointing growpart there instead avoids
# the race entirely rather than trying to out-order it.
export TMPDIR=/run

PARTITION_ONLY=0
if [ "${1:-}" = "--partition-only" ]; then
	PARTITION_ONLY=1
	DATA_SRC="${2:?--partition-only requires a device path}"
else
	MARKER=/data/.data-resized
	[ -f "$MARKER" ] && exit 0
	DATA_SRC="$(findmnt -no SOURCE /data)"
fi

DISK="/dev/$(lsblk -no PKNAME "$DATA_SRC")"
# lsblk's PARTN column isn't available on every util-linux version this
# image might ship (confirmed missing on real hardware: "lsblk: unknown
# column: PARTN") — pull the trailing partition number off the device name
# instead, e.g. /dev/sda4 -> 4, /dev/mmcblk0p4 -> 4.
PART_NUM="${DATA_SRC##*[!0-9]}"

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

if [ "$PARTITION_ONLY" = 1 ]; then
	echo "slide-announcer-data-resize: partition grown, skipping filesystem resize (--partition-only, no filesystem exists yet)"
	exit 0
fi

echo "slide-announcer-data-resize: growing ext4 filesystem on ${DATA_SRC}"
resize2fs "$DATA_SRC"

touch "$MARKER"
echo "slide-announcer-data-resize: done"
