#!/bin/bash
# Runs on-device after files/ is extracted (see make-hotfix-bundle.sh and
# hotfixes/README.md). $ROOT is the patched rootfs's bind-mount, not live /.
set -euo pipefail

# The unit file itself just landed via files/ — enable it the same way
# `systemctl enable` at image-build time would have (see
# stage-slide-announcer/01-system-files/00-run.sh), since a plain file drop
# doesn't create the WantedBy= symlink on its own. Only takes effect on the
# device's next reboot, same as the logind.conf.d drop-in that also landed
# via files/ — this hotfix doesn't try to make either take effect on the
# currently-running session.
systemctl --root="$ROOT" enable slide-announcer-power-button-dirs.service
systemctl --root="$ROOT" enable slide-announcer-power-button.service
