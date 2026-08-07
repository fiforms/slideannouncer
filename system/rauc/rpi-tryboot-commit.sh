#!/bin/bash
# Runs every boot (slide-announcer-tryboot-check.service). No-ops instantly
# on a normal boot. If this boot happened via RAUC's tryboot (staged by
# rpi-tryboot-backend.sh's set-primary), and a health check passes,
# "commits" it: promotes the tryboot'd slot to the one normal boots read,
# and tells RAUC the update is good.
#
# HARDWARE-UNVERIFIED: /proc/device-tree/chosen/bootloader/tryboot is
# reconstructed from Raspberry Pi's general tryboot documentation, not
# confirmed against this image's specific firmware/kernel — verify it
# actually reads "1" after a real tryboot-triggered reboot before relying
# on this. See system/rauc/rpi-tryboot-backend.sh's header and
# SLIDE_ANNOUNCER.md's open questions.
#
# Deliberately stateless w.r.t. /data, same reasoning as
# rpi-tryboot-backend.sh: which slot to commit is read from /proc/cmdline
# (root=LABEL=rootX — ground truth for what actually booted this session),
# not from a marker file set-primary would otherwise have to leave on
# /data. A factory reset that wipes /data mid-tryboot can't desync
# anything this script depends on.
#
# Health check is currently just "we reached this unit" (ordered after the
# backend service) — a placeholder, not a real check of kiosk/network/sync
# health. See SLIDE_ANNOUNCER.md's open questions ("no rollback story...").
set -euo pipefail

TRYBOOT_FLAG="/proc/device-tree/chosen/bootloader/tryboot"
BOOTFW="/boot/firmware"

if [ ! -f "$TRYBOOT_FLAG" ] || [ "$(tr -d '\0' <"$TRYBOOT_FLAG" 2>/dev/null)" != "1" ]; then
	exit 0 # normal boot, nothing to commit
fi

LETTER="$(sed -n 's/.*\broot=LABEL=root\([AB]\)\b.*/\1/p' /proc/cmdline)"
if [ -z "$LETTER" ]; then
	echo "rpi-tryboot-commit.sh: couldn't find root=LABEL=root[AB] in /proc/cmdline — not committing" >&2
	exit 1
fi
SLOT_DIR="${BOOTFW}/slot${LETTER}"

if [ ! -d "$SLOT_DIR" ]; then
	echo "rpi-tryboot-commit.sh: ${SLOT_DIR} missing — not committing" >&2
	exit 1
fi

echo "rpi-tryboot-commit.sh: tryboot into slot ${LETTER} reached this point — committing"

# Kernel/initramfs filenames vary by Pi model (kernel8.img vs
# kernel_2712.img) — copy whatever the slot actually has, not fixed names.
find "$SLOT_DIR" -maxdepth 1 -type f -exec cp -f {} "${BOOTFW}/" \;
if [ -d "${SLOT_DIR}/overlays" ]; then
	mkdir -p "${BOOTFW}/overlays"
	rsync -rt --delete "${SLOT_DIR}/overlays/" "${BOOTFW}/overlays/"
fi

rm -f "${BOOTFW}/tryboot.txt"

if ! rauc status mark-good; then
	echo "rpi-tryboot-commit.sh: boot files committed, but 'rauc status mark-good' failed — check 'rauc status' manually" >&2
	exit 1
fi
