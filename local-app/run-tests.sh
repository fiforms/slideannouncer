#!/bin/bash
# Runs the backend tests — core's and the product's — against a staged copy
# of this repo's backend with PRODUCT_ROOT's backend/ in place, since a
# product's tests import `products.<name>` and need that layout.
#
#   PRODUCT_ROOT=examples/portal local-app/run-tests.sh [pytest args]
#
# With PYTHON unset, uses (creating it on first run) a venv at
# local-app/backend/venv with requirements.txt + pytest; set PYTHON to use
# your own interpreter instead.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${HERE}/product-env.sh"

if [ -z "${PYTHON:-}" ]; then
	VENV="${HERE}/backend/venv"
	if [ ! -x "${VENV}/bin/python" ]; then
		echo "==> Creating ${VENV} (first run)"
		python3 -m venv "$VENV"
		"${VENV}/bin/pip" install -q -r "${HERE}/backend/requirements.txt" pytest
	fi
	PYTHON="${VENV}/bin/python"
fi

BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
stage_local_app "$BUILD"
cd "${BUILD}/backend"
KIOSK_PRODUCT="$PRODUCT" "$PYTHON" -m pytest -p no:cacheprovider "$@"
