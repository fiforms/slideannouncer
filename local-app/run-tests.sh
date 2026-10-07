#!/bin/bash
# Runs the backend tests — core's and the product's — against a staged copy
# of this repo's backend with PRODUCT_ROOT's backend/ in place, since a
# product's tests import `products.<name>` and need that layout.
#
#   PRODUCT_ROOT=examples/portal local-app/run-tests.sh [pytest args]
#
# PYTHON (default python3) must have local-app/backend/requirements.txt and
# pytest installed.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${HERE}/product-env.sh"

BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
stage_local_app "$BUILD"
cd "${BUILD}/backend"
KIOSK_PRODUCT="$PRODUCT" "${PYTHON:-python3}" -m pytest -p no:cacheprovider "$@"
