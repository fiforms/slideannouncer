#!/bin/bash
# Builds the 0.1.4 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="${HERE}/files"

REQUIRED_VERSION="0.1.3"
NEW_VERSION="0.1.4"

"${HERE}/../../make-hotfix-bundle.sh" "$FILES_DIR" "$REQUIRED_VERSION" "$NEW_VERSION" \
	"${HERE}/script.sh"
