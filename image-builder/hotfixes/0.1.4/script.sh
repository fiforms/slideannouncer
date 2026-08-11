#!/bin/bash
# Runs on-device after files/ is extracted (see make-hotfix-bundle.sh and
# hotfixes/README.md). $ROOT is the patched rootfs's bind-mount, not live /.
set -euo pipefail

# The unit file itself just landed via files/ — enable it the same way
# `systemctl enable` at image-build time would have (see
# stage-slide-announcer/01-system-files/00-run.sh), since a plain file drop
# doesn't create the WantedBy= symlink on its own.
systemctl --root="$ROOT" enable slide-announcer-rauc-dirs.service

# Superseded by slide-announcer-rauc-dirs.service — a bare tmpfiles.d rule
# ran before /data was mounted, so /data/rauc never actually got created
# (see system/README.md's slide-announcer-rauc-dirs.service entry).
rm -f "$ROOT/etc/tmpfiles.d/slide-announcer-rauc.conf"
