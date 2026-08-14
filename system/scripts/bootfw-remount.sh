#!/bin/bash
# Flips /boot/firmware between ro (its default — see 00-run.sh's fstab
# entry) and rw, for the handful of things that legitimately write there:
# RAUC's kernel-slot OTA install, rpi-tryboot-backend.sh, rpi-tryboot-commit.sh,
# factory-reset-check.sh, and slide-announcer-factory-reset-trigger.service.
# One implementation shared by all of them rather than each repeating the
# same `mount -o remount` line — callers should pair this with a trap so
# the ro remount still happens if the write in between fails partway:
#   trap 'slide-announcer-bootfw-remount ro' EXIT
#   slide-announcer-bootfw-remount rw
#   ...write...
#
# Not used by RAUC's own hook.sh (baked per-release into build.sh's bundle
# heredoc) — that script can run against a device still on an OS build from
# before this one existed, which wouldn't have this installed yet, so it
# inlines the same `mount -o remount` calls directly instead of depending
# on it.
set -euo pipefail

MODE="${1:?usage: $0 rw|ro}"
case "$MODE" in
rw | ro) ;;
*)
	echo "$0: mode must be rw or ro" >&2
	exit 1
	;;
esac

mount -o "remount,${MODE}" /boot/firmware
