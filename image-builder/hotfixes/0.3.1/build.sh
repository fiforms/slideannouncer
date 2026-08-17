#!/bin/bash
# Builds the 0.3.1 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# Captures the remote's KEY_POWER button and drives a manual sleep/wake
# toggle instead of letting it fall through to systemd-logind's default
# power-key handling (suspend), which the Pi cannot reliably resume from —
# see system/README.md's entries for slide-announcer-power-button.service
# and logind/slide-announcer-power-button.conf for the full design.
#
# - /usr/local/sbin/slide-announcer-display-power: the actual sleep/wake
#   mechanism (stop/start slide-announcer-kiosk.service,
#   vcgencmd display_power on/off), callable directly without sudo by
#   root/slideannouncer/slideadmin — see the script's own docstring. The
#   one thing every trigger (the remote key here, later a schedule or a
#   web UI button) calls instead of each re-implementing the toggle.
# - /usr/local/sbin/slide-announcer-power-button-monitor +
#   /etc/systemd/system/slide-announcer-power-button.service (runs
#   unprivileged, as slideannouncer) +
#   /etc/systemd/system/slide-announcer-power-button-dirs.service (creates
#   /run/slide-announcer, group slideannouncer, for the toggle's
#   sleep-state marker file): the remote-key trigger for the above.
#   script.sh enables both units — a plain file drop doesn't create the
#   WantedBy= symlink on its own.
# - /etc/systemd/logind.conf.d/slide-announcer-power-button.conf:
#   HandlePowerKey=ignore + HandlePowerKeyLongPress=ignore, so logind stops
#   racing the new daemon for the same keypress. Only takes effect once
#   systemd-logind restarts/reboots.
# - /etc/systemd/system/slide-announcer-kiosk.service: adds
#   TimeoutStopSec=10 — this unit is now stopped routinely (every sleep
#   toggle), not just at reboot/shutdown, so a stop can't hang for
#   systemd's 90s default if Chromium is ever unresponsive.
#
# Requires a reboot after install for both the new unit and the logind
# drop-in to take effect (same as the 0.2.8/vtlock hotfix).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="${HERE}/files"
SCRIPT="${HERE}/script.sh"

REQUIRED_VERSION="0.3.0"
NEW_VERSION="0.3.1"

"${HERE}/../../make-hotfix-bundle.sh" "$FILES_DIR" "$REQUIRED_VERSION" "$NEW_VERSION" "$SCRIPT"
