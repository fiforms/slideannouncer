#!/bin/bash
# Builds the 0.1.10 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# Fixes a cosmetic false-negative in `slide-announcer-update tryboot`:
# `systemctl start slide-announcer-tryboot.service` blocks waiting for the
# job to finish, but that unit's own ExecStart reboots the machine, so the
# job sometimes gets canceled by the shutdown before it can report success
# — printing "Failed to start..." even though the reboot already fired.
# Switched to `systemctl start --no-block`, which just enqueues the job and
# returns immediately. Also drops the now-stale "HARDWARE-UNVERIFIED"
# wording from the tryboot warning, since the full cycle is confirmed
# working end-to-end on real hardware as of this hotfix.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="${HERE}/files"

REQUIRED_VERSION="0.1.9"
NEW_VERSION="0.1.10"

"${HERE}/../../make-hotfix-bundle.sh" "$FILES_DIR" "$REQUIRED_VERSION" "$NEW_VERSION"
