#!/bin/bash
# Runs the backend tests — core's and the product's — against a staged copy
# of this repo's backend with PRODUCT_ROOT's backend/ in place, since a
# product's tests import `products.<name>` and need that layout.
#
#   PRODUCT_ROOT=examples/portal local-app/run-tests.sh [pytest args]
#
# With PYTHON unset, uses (creating it on first run) a venv at
# local-app/backend/venv with the merged core + product requirements + pytest; set PYTHON to use
# your own interpreter instead.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${HERE}/product-env.sh"

if [ -z "${PYTHON:-}" ]; then
	VENV="${HERE}/backend/venv"
	REQS="$(mktemp)"
	merged_requirements "$REQS"
	if [ ! -x "${VENV}/bin/python" ]; then
		echo "==> Creating ${VENV} (first run)"
		python3 -m venv "$VENV"
	fi
	# Reinstall when core's or the product's requirements changed.
	if ! cmp -s "$REQS" "${VENV}/requirements.installed"; then
		"${VENV}/bin/pip" install -q -r "$REQS" pytest
		cp "$REQS" "${VENV}/requirements.installed"
	fi
	rm -f "$REQS"
	PYTHON="${VENV}/bin/python"
fi

# The platform checkout, for tests of scripts that live outside the backend
# (system/scripts, updater) and so are not in the staged copy below.
export PLATFORM_ROOT="$(cd "${HERE}/.." && pwd)"

BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
stage_local_app "$BUILD"
cd "${BUILD}/backend"
KIOSK_PRODUCT="$PRODUCT" "$PYTHON" -m pytest -p no:cacheprovider "$@"
