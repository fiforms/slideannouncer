#!/bin/bash
# Runs on-device after files/ is extracted (see make-hotfix-bundle.sh and
# hotfixes/README.md). files/ just landed a brand-new
# /etc/systemd/system/slide-announcer-tryboot.service (see
# hotfixes/0.1.5/script.sh's own comment for why a new file under /etc is
# already visible at the live path with no bind-mount trickery needed —
# same reasoning applies here). systemd still needs telling about it
# though: unlike polkit, which picks up rules.d changes via inotify on its
# own, systemd caches its unit list and won't see a file that appeared
# after the daemon started without an explicit reload. Without this,
# `systemctl start slide-announcer-tryboot.service` fails with "Unit not
# found" until the next reboot.
set -euo pipefail

systemctl daemon-reload
