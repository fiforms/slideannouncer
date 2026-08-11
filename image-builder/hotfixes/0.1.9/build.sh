#!/bin/bash
# Builds the 0.1.9 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# Fixes rpi-tryboot-commit.sh's tryboot-flag check: it compared
# /proc/device-tree/chosen/bootloader/tryboot's raw bytes against the ASCII
# string "1", but that property is a 4-byte big-endian devicetree cell
# (00 00 00 01 when set) — confirmed by testing on real hardware (a tryboot
# reboot landed on the new slot but was never committed: config.txt's
# os_prefix never flipped, tryboot.txt was never removed, the slot's boot
# status stayed "bad"). The check now reads the raw hex bytes instead of
# assuming text.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="${HERE}/files"

REQUIRED_VERSION="0.1.8"
NEW_VERSION="0.1.9"

"${HERE}/../../make-hotfix-bundle.sh" "$FILES_DIR" "$REQUIRED_VERSION" "$NEW_VERSION"
