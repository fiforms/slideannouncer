#!/bin/bash
# Builds the 0.1.7 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# Narrows 0.1.6's over-broad sudo self-elevation in slide-announcer-update:
# `install`/`status`/`mark-good` go through RAUC's own D-Bus service (root
# regardless of caller) and never needed it — only `tryboot`'s raw
# `reboot <cmd>` call does, since that writes straight to
# /run/systemd/reboot-param, bypassing D-Bus entirely.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="${HERE}/files"

REQUIRED_VERSION="0.1.6"
NEW_VERSION="0.1.7"

"${HERE}/../../make-hotfix-bundle.sh" "$FILES_DIR" "$REQUIRED_VERSION" "$NEW_VERSION"
