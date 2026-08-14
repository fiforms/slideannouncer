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
# confirmed working on real hardware for kernel/cmdline/root loading — a
# tryboot-triggered boot correctly loads the staged slot and boots into
# it. What's NOT reliable there is GPU/DRM: a tryboot-*flagged* os_prefix
# boot's DTB-fixup step silently fails to apply the vc4-kms-v3d overlay
# (confirmed by testing — zero DRM devices, no kiosk display), while the
# identical files loaded via a PERMANENT, non-tryboot os_prefix boot
# fine. See system/rauc/rpi-tryboot-commit.sh and repartition.sh's own
# comments for how the overall design accounts for this — a tryboot
# session is only ever used for a brief, headless-acceptable verification
# window, never for anything that needs a working display.
#
# Deliberately stateless w.r.t. /data: get-primary derives "which slot is
# currently running" from /proc/cmdline's rauc.slot=rootfs.N marker, not
# from a stored marker file. /data is a common factory-reset target ("wipe
# and re-pair"), and a marker file there would go stale the moment that
# happens — reporting a slot as primary that isn't actually the one
# mounted as / would make the NEXT install target (i.e. overwrite) the
# slot that's genuinely running right now. Reading the live kernel
# command line instead means there's nothing on /data for a reset to
# desynchronize; the answer is always physically true by construction.
# root=LABEL=rootX was the original source for this — switched to
# rauc.slot= when cmdline.txt's root= itself moved to PARTUUID= (see
# system.conf's own comment for why); confirmed by testing that this
# script was never updated for that at the time, breaking `rauc status`
# outright ("Failed getting primary slot: custom backend:
# rpi-tryboot-backend.sh failed with exit code 1") since current_root_letter
# was still matching a root=LABEL= pattern that no longer exists in
# /proc/cmdline at all.
#
# set-primary only ever stages a *provisional* boot attempt (tryboot.txt,
# read by the firmware for exactly one tryboot-triggered reboot) — it
# deliberately does NOT touch config.txt's own permanent os_prefix= line
# that every *normal* reboot reads (only rpi-tryboot-commit.sh does that,
# and only after a successful trial), and it writes nothing to /data
# either (rpi-tryboot-commit.sh re-derives which slot to commit the same
# way, from /proc/cmdline on the tryboot'd boot itself — see its header).
# So "do nothing after a tryboot attempt" already means "revert" for
# free, and there's no persistent staging state anywhere to lose.
set -euo pipefail

BOOTFW="/boot/firmware"

current_root_letter() {
	local slotnum
	slotnum="$(sed -n 's/.*\brauc\.slot=rootfs\.\([01]\)\b.*/\1/p' /proc/cmdline)"
	case "$slotnum" in
	0) echo A ;;
	1) echo B ;;
	*)
		echo "rpi-tryboot-backend.sh: couldn't find rauc.slot=rootfs.[01] in /proc/cmdline" >&2
		exit 1
		;;
	esac
}

case "${1:-}" in
get-primary | get-current)
	current_root_letter
	;;
set-primary)
	LETTER="${2:?set-primary requires a bootname argument}"
	# Confirmed by testing on real hardware: tryboot.txt must be a full copy
	# of config.txt, not just a bare "[tryboot]\nos_prefix=..." stub — the
	# firmware reads tryboot.txt as a *complete replacement* config for the
	# trial boot, not an overlay merged on top of config.txt. A stub with
	# only os_prefix set silently drops every other config.txt setting for
	# that boot (confirmed cause of the "tryboot hangs, never comes back up"
	# failure), so the only line that may differ from config.txt is
	# os_prefix= itself.
	if ! grep -q '^os_prefix=slot[AB]/$' "${BOOTFW}/config.txt"; then
		echo "rpi-tryboot-backend.sh: no os_prefix=slotA/ or slotB/ line found in ${BOOTFW}/config.txt — refusing to stage tryboot.txt" >&2
		exit 1
	fi
	# BOOTFW is ro by default (see 00-run.sh's fstab entry) — trap ensures
	# the remount back to ro still happens if the sed below fails partway.
	slide-announcer-bootfw-remount rw
	trap 'slide-announcer-bootfw-remount ro' EXIT
	sed -E "s#^os_prefix=slot[AB]/\$#os_prefix=slot${LETTER}/#" "${BOOTFW}/config.txt" >"${BOOTFW}/tryboot.txt"
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
	slide-announcer-bootfw-remount rw
	trap 'slide-announcer-bootfw-remount ro' EXIT
	echo "$STATE" >"${BOOTFW}/state-${LETTER}"
	;;
*)
	echo "usage: $0 get-primary | set-primary <bootname> | get-current | get-state <bootname> | set-state <bootname> good|bad" >&2
	exit 1
	;;
esac
