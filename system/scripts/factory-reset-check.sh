#!/bin/bash
# Runs before /data is ever mounted (slide-announcer-factory-reset-check.service
# — DefaultDependencies=no, ordered Before=data.mount). If
# /boot/firmware/FACTORY_RESET exists, reformats /data with a fresh
# filesystem and removes the flag.
#
# No second reboot needed: once this unit finishes, systemd just proceeds
# to mount the now-empty /data normally, and everything downstream already
# knows how to handle "a fresh /data with nothing on it" correctly, because
# that's exactly what a brand-new SD card looks like too —
# slide-announcer-data-dirs.service recreates the /etc overlay's upper/work
# dirs, slide-announcer-data-resize.service grows the filesystem (its own
# marker file is gone too), slide-announcer-firstboot.service regenerates
# SSH host keys/machine-id/device identity (identity.key is gone, so its
# mismatch check trips unconditionally), and
# slide-announcer-local-app-seed.service re-extracts local-app from the
# image's embedded release. WiFi credentials are gone too, for the same
# reason — NetworkManager's saved connection profiles live under
# /etc/NetworkManager/system-connections/, which is the /etc overlay's
# upperdir, which lives on /data.
#
# The flag can also be created by hand (mount the boot partition on any
# PC/Mac, same as slideannouncer.yaml) — no web UI or running device
# required.
set -euo pipefail

FLAG=/boot/firmware/FACTORY_RESET
[ -f "$FLAG" ] || exit 0

echo "slide-announcer-factory-reset: ${FLAG} present — reformatting /data"

DATA_FSTAB_SRC="$(findmnt --fstab -no SOURCE /data)"
case "$DATA_FSTAB_SRC" in
	PARTUUID=*)
		DATA_DEV="/dev/disk/by-partuuid/${DATA_FSTAB_SRC#PARTUUID=}"
		;;
	*)
		DATA_DEV="$DATA_FSTAB_SRC"
		;;
esac
udevadm settle --timeout=10 || true
DATA_DEV="$(readlink -f "$DATA_DEV")"

if [ -z "$DATA_DEV" ] || [ ! -b "$DATA_DEV" ]; then
	echo "slide-announcer-factory-reset: could not resolve /data's block device from fstab (${DATA_FSTAB_SRC}) — refusing to proceed" >&2
	exit 1
fi

echo "slide-announcer-factory-reset: mkfs.ext4 -F ${DATA_DEV}"
mkfs.ext4 -F -L data "$DATA_DEV"

# /boot/firmware is ro by default (see 00-run.sh's fstab entry) — bracket
# just the flag removal, with a trap so the remount back to ro still
# happens even if `rm` somehow fails.
slide-announcer-bootfw-remount rw
trap 'slide-announcer-bootfw-remount ro' EXIT
rm -f "$FLAG"
echo "slide-announcer-factory-reset: done — /data will mount fresh this boot"
