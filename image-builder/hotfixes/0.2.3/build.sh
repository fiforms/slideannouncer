#!/bin/bash
# Builds the 0.2.3 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# Drops in the fix for the white flash between the boot progress screen's
# dark shades and the kiosk slideshow loading: Chromium's first compositor
# frame is white by default, painted before the page (whose own CSS is
# already all-dark) loads far enough to override it — jarring on a large
# TV. slide-announcer-kiosk-start.sh now passes
# --default-background-color=000000 so that pre-paint default is black
# instead.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="${HERE}/files"

REQUIRED_VERSION="0.2.2"
NEW_VERSION="0.2.3"

"${HERE}/../../make-hotfix-bundle.sh" "$FILES_DIR" "$REQUIRED_VERSION" "$NEW_VERSION"
