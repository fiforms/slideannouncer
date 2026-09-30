#!/bin/bash
# Builds the 0.3.10 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# Updates the Revelation Snapshot Presenter peering daemon to peering
# protocol 2 (doc/dev/PEERING.md in fiforms/revelation-electron-wrapper):
# nonce-based follower signatures via this device's own RSA key, a signed
# POST /peer/socket-info instead of the old GET ?pin=, and no stored PIN.
# The daemon is OS-image infra, not part of the versioned local-app release
# (see its own docstring), so the matching pairing-side changes in
# local-app/backend/revelation.py ship through the app's own updater
# channel — same split the 0.3.3 and 0.3.9 hotfixes used. The two halves
# must land together: the old daemon crashes on a protocol-2 trust record,
# and devices paired under protocol 1 show "needs re-pairing" until
# unpaired and paired again.
#
# - /usr/local/sbin/slide-announcer-revelation-peer: replaced with
#   system/scripts/revelation-peer-daemon.py.
#
# Requires a reboot after install: this hook can't safely restart a
# running unit from the live device shell it runs on (see
# make-hotfix-bundle.sh's own doc comment).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="${HERE}/files"

REQUIRED_VERSION="0.3.9"
NEW_VERSION="0.3.10"

# Keep the shipped copy in lockstep with the source of truth.
install -m 755 "${HERE}/../../../system/scripts/revelation-peer-daemon.py" \
	"${FILES_DIR}/usr/local/sbin/slide-announcer-revelation-peer"

"${HERE}/../../make-hotfix-bundle.sh" "$FILES_DIR" "$REQUIRED_VERSION" "$NEW_VERSION"
