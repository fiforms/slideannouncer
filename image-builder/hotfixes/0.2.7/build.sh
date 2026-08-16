#!/bin/bash
# Builds the 0.2.7 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# Hides the mouse cursor at kiosk boot instead of only once a real mouse
# moves: installs the "slide-announcer-blank" cursor theme (see
# system/cursor-theme/slide-announcer-blank/) — the entire Adwaita cursor
# set with only cursors/default overwritten by a fully transparent 1x1
# image — and points slide-announcer-kiosk-start.sh's
# XCURSOR_THEME/XCURSOR_SIZE at it before labwc starts. Confirmed on real
# hardware that Chromium renders a cursor name this theme doesn't define at
# all as nothing rather than falling back to its own bundled cursors, so
# every other shape (hover, text, busy, resize, …) needs a real Adwaita
# image here too, not just the idle arrow. A real mouse still moves/clicks
# normally throughout; only the idle arrow is invisible, and from the very
# first compositor frame — not only after a genuine pointer event, unlike
# the kiosk view's existing CSS `cursor: none`.
#
# This device (already at 0.2.6, predating adwaita-icon-theme's addition to
# 00-packages) doesn't have the Adwaita cursor files installed to copy
# from, and a hotfix has no business apt-installing a new package onto a
# live rootfs (see hotfixes/README.md and 00-run.sh's own "never install
# via apt on a running device" convention). So this fetches
# adwaita-icon-theme's .deb here, at bundle-build time on this dev
# machine — which does have internet — and stages its cursors/ straight
# into the bundle, the same way 00-run.sh copies from the chroot's already-
# installed copy for a full image build. Nothing from that .deb is
# committed to git; only cursors/default (the blank override) is.
#
# local-app/ itself (the earlier, ultimately ineffective synthetic-
# mousemove attempt at this same fix, reverted in Slideshow.vue) is NOT
# part of this hotfix — that ships via the local-app self-updater's own
# Tier 2 channel instead, same as every other local-app-only change.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../../.." && pwd)"

REQUIRED_VERSION="0.2.6"
NEW_VERSION="0.2.7"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

STAGE="${WORK}/files"
cp -a "${HERE}/files/." "$STAGE"

CURSORS_DIR="${STAGE}/usr/share/icons/slide-announcer-blank/cursors"
mkdir -p "$CURSORS_DIR"

echo "==> Fetching adwaita-icon-theme (Architecture: all — same content regardless of host arch)"
DEB_DIR="${WORK}/deb"
mkdir -p "$DEB_DIR"
( cd "$DEB_DIR" && apt-get download adwaita-icon-theme )
DEB_FILE="$(ls "${DEB_DIR}"/adwaita-icon-theme_*.deb)"

EXTRACT_DIR="${WORK}/extract"
dpkg-deb -x "$DEB_FILE" "$EXTRACT_DIR"
cp -a "${EXTRACT_DIR}/usr/share/icons/Adwaita/cursors/." "${CURSORS_DIR}/"

# Overwrite with our own blank image last, same as 00-run.sh — Adwaita's
# left_ptr/top_left_arrow/arrow/move/dnd-move are symlinks to default
# already, so this one file blanks all of them for free.
install -m 644 \
	"${REPO_ROOT}/system/cursor-theme/slide-announcer-blank/cursors/default" \
	"${CURSORS_DIR}/default"

"${HERE}/../../make-hotfix-bundle.sh" "$STAGE" "$REQUIRED_VERSION" "$NEW_VERSION"
