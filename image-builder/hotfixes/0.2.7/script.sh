#!/bin/bash
# Runs on-device after files/ is extracted (see make-hotfix-bundle.sh and
# hotfixes/README.md).
#
# files/ dropped the "slide-announcer-blank" cursor theme's index.theme and
# its one real image (cursors/default — a fully transparent 1x1 pixel).
# left_ptr/top_left_arrow are just synonyms for the same idle-arrow shape,
# so create them here as symlinks rather than checking in byte-identical
# binary files (see system/cursor-theme/slide-announcer-blank/) — the same
# approach 01-system-files/00-run.sh uses for a fresh image build.
#
# Deliberately not extended to pointer/text/wait/resize/etc.: Chromium
# falls back to its own bundled visible bitmap cursors for any name this
# theme doesn't define, so leaving those out keeps normal cursor feedback
# (link hover, text fields, busy spinner) everywhere outside the kiosk view.
set -euo pipefail

CURSORS_DIR="${ROOT:?}/usr/share/icons/slide-announcer-blank/cursors"

for name in left_ptr top_left_arrow; do
	ln -sf default "${CURSORS_DIR}/${name}"
done
