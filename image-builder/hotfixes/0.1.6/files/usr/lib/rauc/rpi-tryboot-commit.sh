#!/bin/bash
# Runs every boot (slide-announcer-tryboot-check.service). No-ops instantly
# on a normal boot. If this boot happened via RAUC's tryboot (staged by
# rpi-tryboot-backend.sh's set-primary), and a health check passes,
# "commits" it: copies tryboot.txt over config.txt (making the os_prefix=
# swap permanent), tells RAUC the update is good, then immediately reboots
# normally (not via tryboot) so the device actually starts using it.
#
# This does NOT copy the slot's kernel/initramfs/.dtbs/overlays/cmdline.txt
# anywhere — an earlier version promoted them to the boot partition's top
# level via cp/rsync, creating a third, ad hoc "promoted" copy alongside
# slotA/ and slotB/. Confirmed by testing on real hardware that this was
# unnecessary AND that the tryboot-flagged os_prefix path itself has a
# real bug (see below) — repartition.sh's own comment covers the full
# picture, but in short: slotA/slotB are now the only real copies, ever,
# and "committing" just means changing which one config.txt's permanent
# os_prefix= points at.
#
# Immediate reboot after commit is deliberate, not optional: confirmed by
# testing that a tryboot-*flagged* boot's os_prefix loads kernel/cmdline/
# root just fine, but its DTB-fixup step silently fails to apply the
# vc4-kms-v3d overlay — zero DRM devices, no kiosk display — a Raspberry
# Pi firmware quirk specific to the tryboot flag itself, not to os_prefix
# in general (a PERMANENT, non-tryboot os_prefix pointing at the exact
# same files boots with working GPU every time). So a tryboot session is
# only ever good for a brief, headless-acceptable verification window
# (does the kernel boot, does root mount, does the rest of the system
# come up) — it deliberately does NOT need to look right on screen, and
# shouldn't be left running any longer than the health check takes: this
# is a signage display, not something that should sit headless post-commit
# until whatever reboot happens to occur next.
#
# Deliberately stateless w.r.t. /data, same reasoning as
# rpi-tryboot-backend.sh: which slot to commit is read from /proc/cmdline
# (rauc.slot=rootfs.N — ground truth for what actually booted this
# session), not from a marker file set-primary would otherwise have to
# leave on /data. A factory reset that wipes /data mid-tryboot can't
# desync anything this script depends on.
#
# Health check is currently just "we reached this unit" (ordered after the
# backend service) — a placeholder, not a real check of network/backend/
# sync health. See SLIDE_ANNOUNCER.md's open questions ("no rollback
# story...").
set -euo pipefail

TRYBOOT_FLAG="/proc/device-tree/chosen/bootloader/tryboot"
BOOTFW="/boot/firmware"

if [ ! -f "$TRYBOOT_FLAG" ] || [ "$(tr -d '\0' <"$TRYBOOT_FLAG" 2>/dev/null)" != "1" ]; then
	exit 0 # normal boot, nothing to commit
fi

SLOTNUM="$(sed -n 's/.*\brauc\.slot=rootfs\.\([01]\)\b.*/\1/p' /proc/cmdline)"
case "$SLOTNUM" in
0) LETTER=A ;;
1) LETTER=B ;;
*)
	echo "rpi-tryboot-commit.sh: couldn't find rauc.slot=rootfs.[01] in /proc/cmdline — not committing" >&2
	exit 1
	;;
esac
SLOT_DIR="${BOOTFW}/slot${LETTER}"

if [ ! -d "$SLOT_DIR" ]; then
	echo "rpi-tryboot-commit.sh: ${SLOT_DIR} missing — not committing" >&2
	exit 1
fi

echo "rpi-tryboot-commit.sh: tryboot into slot ${LETTER} reached this point — committing"

# tryboot.txt (staged by rpi-tryboot-backend.sh's set-primary) is already a
# full, valid config.txt with only os_prefix swapped — confirmed by testing
# that committing must be a straight copy over config.txt, not a sed edit of
# it in place. A sed edit re-derives the change from a regex match against
# config.txt's *current* os_prefix= line, which duplicates logic that
# already ran once when tryboot.txt was staged and can drift from it (e.g.
# if config.txt was hand-edited in between); copying the file that was
# actually booted from removes that whole class of mismatch.
if [ ! -f "${BOOTFW}/tryboot.txt" ]; then
	echo "rpi-tryboot-commit.sh: ${BOOTFW}/tryboot.txt missing — not committing" >&2
	exit 1
fi
cp "${BOOTFW}/tryboot.txt" "${BOOTFW}/config.txt"
rm -f "${BOOTFW}/tryboot.txt"

if ! rauc status mark-good; then
	echo "rpi-tryboot-commit.sh: config.txt committed, but 'rauc status mark-good' failed — check 'rauc status' manually" >&2
	exit 1
fi

echo "rpi-tryboot-commit.sh: committed slot ${LETTER} — rebooting normally to actually use it"
reboot
