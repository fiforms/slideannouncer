#!/bin/bash
# Builds the 0.4.1 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# Teaches the on-device app updater and the boot-time app seeder to compare
# versions as the PAIR <platform>_<product> (e.g. 0.4.0_0.1.1) instead of the
# platform X.Y.Z alone, so a product-only app release (same platform, newer
# product) is seen as an update. A version with no product part counts as
# product 0.0.0, so the plain X.Y.Z releases already on devices still compare
# correctly. Both scripts are OS-image infrastructure on the root filesystem,
# which is why this has to ship as a hotfix rather than an app update.
#
# - /usr/local/sbin/slide-announcer-local-app-seed: replaced with
#   system/scripts/local-app-seed.py (takes effect on the next boot).
# - /opt/slide-announcer/updater/local_app_updater.py: replaced with
#   updater/local_app_updater.py (the updater timer starts it fresh each run,
#   so no reboot is needed for it).
#
# This is also the hotfix that moves devices to the pair-versioned OS
# identity: /opt/slide-announcer/VERSION goes from the plain 0.4.0 to
# 0.4.1_<product image version> (here the product's image/VERSION, 0.1.0 —
# build with PRODUCT_ROOT set — from the outer repo,
# `npm run hotfix:build:core 0.4.1` does). From here on a hotfix
# gates on, and bumps, the pair exactly: 0.4.1_0.1.0 -> 0.4.1_0.1.1 for a
# product-only change, 0.4.1_0.1.0 -> 0.4.2_0.1.0 for a platform-only one.
#
# The server must already accept pair versions (release uploads and hotfix
# matching) before this is published — see the outer repo's
# SlideAnnouncerRelease.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../../.." && pwd)"
# PRODUCT_ROOT is required (product-env.sh checks it); it supplies the
# product's image version for the pair.
# shellcheck disable=SC1091
. "${REPO_ROOT}/local-app/product-env.sh"

# Devices still on the pre-pair image read the plain platform version.
REQUIRED_VERSION="0.4.0"
NEW_VERSION="$(compose_version 0.4.1 "$(product_image_version)")"

# Staged in a temp dir (not committed): the shipped copies always come
# straight from the source of truth, as the 0.3.10 hotfix did.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
install -D -m 755 "${REPO_ROOT}/system/scripts/local-app-seed.py" \
	"${STAGE}/usr/local/sbin/slide-announcer-local-app-seed"
install -D -m 644 "${REPO_ROOT}/updater/local_app_updater.py" \
	"${STAGE}/opt/slide-announcer/updater/local_app_updater.py"

"${HERE}/../../make-hotfix-bundle.sh" "$STAGE" "$REQUIRED_VERSION" "$NEW_VERSION"
