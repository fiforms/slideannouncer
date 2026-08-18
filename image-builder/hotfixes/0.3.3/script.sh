#!/bin/bash
# Runs on-device after files/ is extracted (see make-hotfix-bundle.sh and
# hotfixes/README.md). $ROOT is the patched rootfs's bind-mount, not live /.
set -euo pipefail

# The unit file itself just landed via files/ — enable it the same way
# `systemctl enable` at image-build time would have (see
# stage-slide-announcer/01-system-files/00-run.sh), since a plain file drop
# doesn't create the WantedBy= symlink on its own. Only takes effect on the
# device's next reboot, same as the 0.3.1/power-button hotfix this mirrors.
systemctl --root="$ROOT" enable slide-announcer-volume-key.service
