#!/bin/bash
# Builds the 0.1.8 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# Adds slide-announcer-tryboot.service (a root oneshot unit that runs
# `reboot "0 tryboot"`) plus polkit coverage for slideannouncer/slideadmin
# to start it, and updates slide-announcer-update's `tryboot` subcommand to
# go through that unit (`systemctl start`) instead of calling `reboot`
# directly — removes the sudo prompt tryboot still needed after 0.1.7.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="${HERE}/files"

REQUIRED_VERSION="0.1.7"
NEW_VERSION="0.1.8"

"${HERE}/../../make-hotfix-bundle.sh" "$FILES_DIR" "$REQUIRED_VERSION" "$NEW_VERSION" \
	"${HERE}/script.sh"
