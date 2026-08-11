#!/bin/bash
# Builds the 0.1.6 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="${HERE}/files"

REQUIRED_VERSION="0.1.5"
NEW_VERSION="0.1.6"

"${HERE}/../../make-hotfix-bundle.sh" "$FILES_DIR" "$REQUIRED_VERSION" "$NEW_VERSION"
