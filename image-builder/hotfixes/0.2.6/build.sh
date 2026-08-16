#!/bin/bash
# Builds the 0.2.6 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# Carries two OS-image-side fixes onto an already-provisioned device
# without a full reimage:
#
# - updater/local_app_updater.py: the local-app self-updater demanded exact
#   string equality between the server's declared version tag (e.g.
#   "0.2.6") and a release tarball's own VERSION content, which
#   local-app/package.sh always suffixes with a git hash (and "-dirty" if
#   applicable) — so no release built by this project's own tooling could
#   ever pass. Also read update-availability only from heartbeat.py's own
#   up-to-5-minutes-stale background cache, so clicking "Update Now" right
#   after a fresh "Check for Update" could silently no-op against
#   pre-release info. Both fixed — see the file's own version_core()/
#   read_heartbeat_update_info() comments.
# - provisioning/firstboot.py: /boot/firmware writes now go through
#   slide-announcer-bootfw-remount (a write this file makes was missed in
#   the original /boot/firmware-read-only-by-default change), and
#   setup-mode detection no longer depends on a slideannouncer.yaml wifi:
#   block that no longer exists (WiFi pre-provisioning moved to
#   network-config) — it polls NetworkManager instead, but only when
#   network-config actually has something configured, so a device with
#   nothing pre-provisioned doesn't pay a wasted ~15s boot delay.
#
# REQUIRED_VERSION=0.2.5 is this project's own best reconstruction of what
# the target test device is actually running (the last full image
# build+reflash predates the 0.2.6 version bump) — confirm against
# `cat /opt/slide-announcer/VERSION` on the device before installing this;
# the hotfix hook refuses to apply against any other version anyway, so a
# wrong guess here just fails loudly rather than silently.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="${HERE}/files"

REQUIRED_VERSION="0.2.5"
NEW_VERSION="0.2.6"

"${HERE}/../../make-hotfix-bundle.sh" "$FILES_DIR" "$REQUIRED_VERSION" "$NEW_VERSION"
