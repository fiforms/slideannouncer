#!/bin/bash
# RAUC custom-bootloader backend for Raspberry Pi tryboot A/B switching.
# RAUC has no built-in tryboot support, so system.conf's
# bootloader-custom-backend points here. Invoked by RAUC itself as:
#   rpi-tryboot-backend.sh get-primary
#   rpi-tryboot-backend.sh set-primary <slot-name>
#
# HARDWARE-UNVERIFIED: this argv contract (get-primary/set-primary, slot
# name on stdout/argv) is reconstructed from RAUC's general custom-
# bootloader conventions, not confirmed against the installed rauc
# version's own docs — check `man rauc` / rauc's source for the exact
# calling convention before trusting this on real hardware. Likewise
# `reboot "0 tryboot"` and the tryboot.txt/os_prefix mechanism below are
# reconstructed from Raspberry Pi's general tryboot documentation, not
# confirmed against this image's specific firmware/kernel. See
# SLIDE_ANNOUNCER.md's open questions — a real tryboot cycle (flash,
# install, tryboot reboot, forced-bad health check, confirm fallback)
# still needs to be run on actual hardware.
#
# Deliberately stateless w.r.t. /data: get-primary derives "which slot is
# currently running" from /proc/cmdline's root=LABEL=rootX, not from a
# stored marker file. /data is a common factory-reset target ("wipe and
# re-pair"), and a marker file there would go stale the moment that
# happens — reporting a slot as primary that isn't actually the one
# mounted as / would make the NEXT install target (i.e. overwrite) the
# slot that's genuinely running right now. Reading the live kernel
# command line instead means there's nothing on /data for a reset to
# desynchronize; the answer is always physically true by construction.
#
# set-primary only ever stages a *provisional* boot attempt (tryboot.txt,
# read by the firmware for exactly one tryboot-triggered reboot) — it
# deliberately does NOT touch the partition-root config.txt/cmdline.txt/
# kernel/initramfs that every *normal* reboot reads, and it writes nothing
# to /data either (rpi-tryboot-commit.sh re-derives which slot to commit
# the same way, from /proc/cmdline on the tryboot'd boot itself — see its
# header). So "do nothing after a tryboot attempt" already means "revert"
# for free, and there's no persistent staging state anywhere to lose.
set -euo pipefail

BOOTFW="/boot/firmware"

slot_to_letter() {
	case "$1" in
	rootfs.0) echo A ;;
	rootfs.1) echo B ;;
	*)
		echo "rpi-tryboot-backend.sh: unknown slot '$1'" >&2
		exit 1
		;;
	esac
}

current_root_letter() {
	local label
	label="$(sed -n 's/.*\broot=LABEL=root\([AB]\)\b.*/\1/p' /proc/cmdline)"
	if [ -z "$label" ]; then
		echo "rpi-tryboot-backend.sh: couldn't find root=LABEL=root[AB] in /proc/cmdline" >&2
		exit 1
	fi
	echo "$label"
}

case "${1:-}" in
get-primary)
	case "$(current_root_letter)" in
	A) echo rootfs.0 ;;
	B) echo rootfs.1 ;;
	esac
	;;
set-primary)
	TARGET_SLOT="${2:?set-primary requires a slot name argument}"
	LETTER="$(slot_to_letter "$TARGET_SLOT")"
	printf '[tryboot]\nos_prefix=slot%s/\n' "$LETTER" >"${BOOTFW}/tryboot.txt"
	echo "rpi-tryboot-backend.sh: staged slot ${LETTER} (${TARGET_SLOT}) for the next tryboot reboot" >&2
	;;
*)
	echo "usage: $0 get-primary | set-primary <slot-name>" >&2
	exit 1
	;;
esac
