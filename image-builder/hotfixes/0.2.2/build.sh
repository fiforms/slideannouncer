#!/bin/bash
# Builds the 0.2.2 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# Drops in the fixes for a real production failure (see this repo's
# troubleshooting notes / SLIDE_ANNOUNCER.md): rauc install failing with
# "Destination device ... for slot 'rootfs.0' not found" on any device
# past its first full-OS OTA, because /etc/rauc/system.conf's
# [slot.rootfs.0]/[slot.rootfs.1] device= lines were never corrected for
# the real device the way fstab/cmdline.txt already are — script.sh fixes
# that directly on-device (build.sh's hook.sh got the equivalent fix for
# all future OTA installs).
#
# Also carries the on-demand "Update Now" plumbing added alongside that
# investigation: two new systemd units
# (slide-announcer-os-updater-now.service,
# slide-announcer-local-app-updater-now.service, both --force variants of
# the existing timer-driven updaters — see either .service file's own
# header) plus the os-updater.py/local_app_updater.py/update-check.py
# changes those units and the Settings UI's progress bar depend on
# (--force flag, flock-based mutual exclusion, RAUC install progress
# parsing, update-progress.json). local-app/ itself (the FastAPI
# backend/Vue frontend) is NOT part of this hotfix — those changes ride
# along on the local-app self-updater's own Tier 2 channel instead, not
# this Tier 1 OS hotfix mechanism.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="${HERE}/files"

REQUIRED_VERSION="0.2.1"
NEW_VERSION="0.2.2"

"${HERE}/../../make-hotfix-bundle.sh" "$FILES_DIR" "$REQUIRED_VERSION" "$NEW_VERSION" \
	"${HERE}/script.sh"
