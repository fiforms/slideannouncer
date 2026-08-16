#!/bin/bash
# Builds the 0.2.6 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# Carries four OS-image-side fixes onto an already-provisioned device
# without a full reimage, all found chasing real "Update Now"/pairing
# failures on a live test device:
#
# - updater/local_app_updater.py: the local-app self-updater demanded exact
#   string equality between the server's declared version tag (e.g.
#   "0.2.6") and a release tarball's own VERSION content, which
#   local-app/package.sh always suffixes with a git hash (and "-dirty" if
#   applicable) — so no release built by this project's own tooling could
#   ever pass. Fixed via the same version_core()-based comparison
#   local-app-seed.py already used correctly.
# - system/scripts/os-updater.py and updater/local_app_updater.py both read
#   update-availability only from heartbeat.py's own up-to-5-minutes-stale
#   background cache — so clicking "Update Now" right after a fresh "Check
#   for Update" found a brand new release could silently no-op (exit
#   before ever writing progress, leaving the two tiers' shared
#   PROGRESS_FILE showing stale content from whichever tier last actually
#   ran) even though `slide-announcer-update install` — always a live
#   heartbeat call, no caching — installed the exact same release fine
#   moments later. Both now prefer update-check.json (fresh at the moment
#   "Update Now" became clickable) on the on-demand/--force path.
# - Same two files also share a lock file (/data/status/update.lock)
#   across two different users — os-updater.py runs as root (needs `rauc
#   install`), local_app_updater.py as the unprivileged `slideannouncer`.
#   Whichever tier created it first left it at default (umask-masked)
#   permissions unusable by the other — PermissionError, not a lock-
#   contention BlockingIOError, so it looked like a crash. Both now force
#   0o666 on it explicitly at creation, bypassing umask.
# - system/scripts/local-app-seed.py (runs as root every boot) chmods
#   /data/local-app and /data/local-app/releases to 0o755, root-owned —
#   fine as long as only this root-run process ever wrote there, but
#   local_app_updater.py now also writes directly into both (as
#   `slideannouncer`, unprivileged) and got a plain PermissionError. Both
#   directories are now chgrp'd to `slideannouncer` and mode 0o775 instead
#   — self-heals on this fix's first boot after install, since this script
#   reapplies both unconditionally every time.
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
