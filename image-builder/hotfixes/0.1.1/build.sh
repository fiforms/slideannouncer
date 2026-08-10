#!/bin/bash
# Builds the 0.1.1 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="${HERE}/files"

REQUIRED_VERSION="0.1.0"
NEW_VERSION="0.1.1"

# /var/hotfix011_files is meant to land empty on the device, and git can't
# track an empty directory — create it here at build time instead of
# committing a placeholder file that would defeat the point.
mkdir -p "${FILES_DIR}/var/hotfix011_files"

"${HERE}/../../make-hotfix-bundle.sh" "$FILES_DIR" "$REQUIRED_VERSION" "$NEW_VERSION"
