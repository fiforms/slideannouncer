#!/bin/bash
# Builds the 0.4.2 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# Sets the WiFi regulatory domain (SLIDE_ANNOUNCER_WIFI_COUNTRY from
# image-builder/.env, default US, baked into the bundle at build time) on the kernel cmdline of both boot
# slots, and on the running kernel via `iw reg set`. See script.sh. No files
# are shipped; this is a boot-partition edit only. The cmdline token takes
# effect on the next boot.
#
# Build with PRODUCT_ROOT set (the product's image/VERSION makes the pair);
# from the outer repo, `npm run hotfix:build:core 0.4.2` does.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../../.." && pwd)"
# shellcheck disable=SC1091
. "${REPO_ROOT}/local-app/product-env.sh"

# Same source, default and validation as the image build (build.sh). The
# device can't read .env, so the value is baked into this bundle's script.
if [ -f "${HERE}/../../.env" ]; then
	set -a
	# shellcheck disable=SC1091
	. "${HERE}/../../.env"
	set +a
fi
SLIDE_ANNOUNCER_WIFI_COUNTRY="${SLIDE_ANNOUNCER_WIFI_COUNTRY:-US}"
if ! [[ "$SLIDE_ANNOUNCER_WIFI_COUNTRY" =~ ^[A-Z]{2}$ ]]; then
	echo "build.sh: SLIDE_ANNOUNCER_WIFI_COUNTRY must be a 2-letter ISO 3166-1 code (e.g. US), got: ${SLIDE_ANNOUNCER_WIFI_COUNTRY}" >&2
	exit 1
fi
echo "==> WiFi regulatory domain: ${SLIDE_ANNOUNCER_WIFI_COUNTRY}"

IMAGE_VERSION="$(product_image_version)"
REQUIRED_VERSION="$(compose_version 0.4.1 "$IMAGE_VERSION")"
NEW_VERSION="$(compose_version 0.4.2 "$IMAGE_VERSION")"

# No files to ship; an empty stage (0755, see README: never change /'s mode).
STAGE="$(mktemp -d)"
chmod 755 "$STAGE"
SCRIPT="$(mktemp)"
trap 'rm -rf "$STAGE" "$SCRIPT"' EXIT
sed "s|@@WIFI_COUNTRY@@|${SLIDE_ANNOUNCER_WIFI_COUNTRY}|" "${HERE}/script.sh" > "$SCRIPT"

"${HERE}/../../make-hotfix-bundle.sh" "$STAGE" "$REQUIRED_VERSION" "$NEW_VERSION" "$SCRIPT"
