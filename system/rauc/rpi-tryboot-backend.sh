#!/bin/bash
# RAUC custom-bootloader backend for Raspberry Pi tryboot A/B switching.
# RAUC has no built-in tryboot support, so system.conf's
# bootloader-custom-backend points here. Invoked by RAUC itself as:
#   rpi-tryboot-backend.sh get-primary
#   rpi-tryboot-backend.sh set-primary <bootname>
#   rpi-tryboot-backend.sh get-current
#   rpi-tryboot-backend.sh get-state <bootname>
#   rpi-tryboot-backend.sh set-state <bootname> good|bad
#
# get-state/set-state are not optional — confirmed by testing: RAUC calls
# set-state <target-bootname> bad on the inactive slot right before
# writing to it (so a crashed/partial install can't leave a slot looking
# bootable), and treats a failing call as fatal to the whole install
# ("Failed marking slot rootfs.1 non-bootable... Child process exited with
# code 1" — this script didn't handle set-state at all until this state
# was added). Unlike get-primary/set-primary, "good"/"bad" genuinely can't
# be derived from any live fact (there's nothing about a slot's own
# contents that says whether it passed its last boot), so this is the one
# piece of real persistent state this backend keeps. It lives on
# /boot/firmware/ (state-A / state-B), not /data — same reasoning as
# tryboot.txt below: /data is a factory-reset target and this state must
# survive that, plus it needs to be readable/writable regardless of which
# rootfs slot is currently active, which only the shared boot partition
# guarantees. Missing state file defaults to "good" (an untouched slot,
# most notably a device's original factory-shipped rootA, has never
# failed anything and was never marked bad).
#
# get-primary/set-primary exchange bootnames ("A"/"B"), NOT slot names
# ("rootfs.0"/"rootfs.1") — confirmed against RAUC 1.13's own source
# (src/bootloaders/custom.c): get-primary's stdout is compared directly
# against each slot's configured bootname= (g_strcmp0(ret_str,
# slot->bootname)), and set-primary is invoked as
# custom_backend_set("set-primary", slot->bootname, ...) — i.e. RAUC hands
# this script the bootname it already resolved from system.conf, not the
# slot's own class.index identifier. An earlier version of this script
# guessed slot names instead and RAUC rejected the result with "custom
# backend: 'rootfs.0' does not match any configured bootname" on real
# hardware — that guess is gone now, this is the confirmed contract.
#
# `reboot "0 tryboot"` and the tryboot.txt/os_prefix mechanism below are
# still reconstructed from Raspberry Pi's general tryboot documentation,
# not confirmed against this image's specific firmware/kernel. See
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
get-primary | get-current)
	current_root_letter
	;;
set-primary)
	LETTER="${2:?set-primary requires a bootname argument}"
	printf '[tryboot]\nos_prefix=slot%s/\n' "$LETTER" >"${BOOTFW}/tryboot.txt"
	echo "rpi-tryboot-backend.sh: staged slot ${LETTER} for the next tryboot reboot" >&2
	;;
get-state)
	LETTER="${2:?get-state requires a bootname argument}"
	if [ -f "${BOOTFW}/state-${LETTER}" ]; then
		cat "${BOOTFW}/state-${LETTER}"
	else
		echo good
	fi
	;;
set-state)
	LETTER="${2:?set-state requires a bootname argument}"
	STATE="${3:?set-state requires good or bad}"
	echo "$STATE" >"${BOOTFW}/state-${LETTER}"
	;;
*)
	echo "usage: $0 get-primary | set-primary <bootname> | get-current | get-state <bootname> | set-state <bootname> good|bad" >&2
	exit 1
	;;
esac
