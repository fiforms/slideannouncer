#!/bin/bash
# Builds the 0.2.8 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# system/polkit/50-slide-announcer-system.rules: grants
# org.freedesktop.login1.reboot-multiple-sessions alongside the existing
# plain reboot grant. Without it, `systemctl reboot` as the unprivileged
# `slideannouncer` user is refused outright by logind (not polkit) any time
# another user session exists at the same time — e.g. an admin's SSH
# session — with "User X is logged in on sshd ... retry after closing
# inhibitors", which is what "Restart Device" in the web UI hit on a real
# device (0.2.7) with an SSH session open.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="${HERE}/files"

REQUIRED_VERSION="0.2.7"
NEW_VERSION="0.2.8"

"${HERE}/../../make-hotfix-bundle.sh" "$FILES_DIR" "$REQUIRED_VERSION" "$NEW_VERSION"
