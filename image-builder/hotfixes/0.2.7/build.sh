#!/bin/bash
# Builds the 0.2.7 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# Hides the mouse cursor at kiosk boot instead of only once a real mouse
# moves: installs the "slide-announcer-blank" cursor theme (see
# system/cursor-theme/slide-announcer-blank/), which blanks only the idle
# arrow (default/left_ptr/top_left_arrow) to a fully transparent 1x1 image,
# and points slide-announcer-kiosk-start.sh's XCURSOR_THEME/XCURSOR_SIZE at
# it before labwc starts. Every other cursor name is deliberately left
# undefined, so Chromium falls back to its own bundled visible cursors for
# hover/text/busy states — link hover on Settings/Pairing still shows
# normal feedback. A real mouse still moves/clicks normally throughout;
# only the idle arrow is invisible, and from the very first compositor
# frame — not only after a genuine pointer event, unlike the kiosk view's
# existing CSS `cursor: none`.
#
# local-app/ itself (the earlier, ultimately ineffective synthetic-
# mousemove attempt at this same fix, reverted in Slideshow.vue) is NOT
# part of this hotfix — that ships via the local-app self-updater's own
# Tier 2 channel instead, same as every other local-app-only change.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="${HERE}/files"

REQUIRED_VERSION="0.2.6"
NEW_VERSION="0.2.7"

"${HERE}/../../make-hotfix-bundle.sh" "$FILES_DIR" "$REQUIRED_VERSION" "$NEW_VERSION" \
	"${HERE}/script.sh"
