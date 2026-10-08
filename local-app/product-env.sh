# Sourced by local-app/{package,dev-deploy,run-tests}.sh and
# image-builder/build.sh: resolves the product being built (docs/PRODUCTS.md).
#
#   PRODUCT_ROOT   directory holding the product — required. Layout:
#                    image/      product.env, packages, enable, system/
#                    backend/    a Python package (__init__.py exposing `product`)
#                    frontend/   index.js and the product's Vue code
#   PRODUCT        its name (default: PRODUCT_ROOT's basename) — the package
#                  directory name, and written into the release's PRODUCT file.
#
# The product lives outside this repo; stage_local_app copies it into a
# throwaway build tree beside the core sources, so this repo's tree is never
# touched and Node/Python resolve the product's imports as if it were inside.

: "${PRODUCT_ROOT:?set PRODUCT_ROOT to a product directory (see docs/PRODUCTS.md; examples/portal is a minimal one)}"
PRODUCT_ROOT="$(cd "$PRODUCT_ROOT" && pwd)"
PRODUCT="${PRODUCT:-$(basename "$PRODUCT_ROOT")}"
for _d in image backend frontend; do
	if [ ! -d "${PRODUCT_ROOT}/${_d}" ]; then
		echo "PRODUCT_ROOT ${PRODUCT_ROOT} has no ${_d}/ directory" >&2
		exit 1
	fi
done
if [ ! -f "${PRODUCT_ROOT}/image/product.env" ]; then
	echo "PRODUCT_ROOT ${PRODUCT_ROOT} has no image/product.env" >&2
	exit 1
fi
export PRODUCT_ROOT PRODUCT

# RAUC `compatible` string: stamped into the image's /etc/rauc/system.conf
# and into every bundle manifest, so a device only installs bundles built
# for its own product. Defaults to <product>-rpi4 (for the original
# slideannouncer product that is the string already in the field); set
# RAUC_COMPATIBLE to override.
RAUC_COMPATIBLE="${RAUC_COMPATIBLE:-${PRODUCT}-rpi4}"
export RAUC_COMPATIBLE

# Product versions (docs/PRODUCTS.md). Two optional files, tracked separately:
#   PRODUCT_ROOT/VERSION         the product's app: its backend + frontend
#   PRODUCT_ROOT/image/VERSION   the product's OS-level files (image/ seam)
# A device's version is the pair <platform>_<product> (e.g. 0.4.0_0.1.1): the
# local app is local-app/VERSION + the product's VERSION, the OS image is
# image-builder/VERSION + the product's image/VERSION. A product with no such
# file has a plain platform version, as before.
_read_version() {
	[ -f "$1" ] && tr -d '[:space:]' < "$1" || true
}
product_app_version() { _read_version "${PRODUCT_ROOT}/VERSION"; }
product_image_version() { _read_version "${PRODUCT_ROOT}/image/VERSION"; }

# compose_version <platform version> <product version>: <platform>_<product>,
# or just the platform version when the product has none.
compose_version() {
	if [ -n "${2:-}" ]; then echo "${1}_${2}"; else echo "$1"; fi
}

# product_version_suffix: appended to the local-app version after the
# <platform>_<product> pair —
#   <product>[-<hash>[-dirty]]
# where the hash is the last commit touching PRODUCT_ROOT.
# PRODUCT_VERSION_SUFFIX overrides the whole thing; the hash is omitted when
# PRODUCT_ROOT isn't in a git repo. Informational: updates compare only the
# leading <platform>_<product> pair (local-app-seed.py's version_core()).
product_version_suffix() {
	if [ -n "${PRODUCT_VERSION_SUFFIX:-}" ]; then
		echo "$PRODUCT_VERSION_SUFFIX"
		return
	fi
	local suffix="$PRODUCT" hash dirty=""
	hash="$(git -C "$PRODUCT_ROOT" log -1 --format=%h -- . 2>/dev/null)" || true
	if [ -n "$hash" ]; then
		[ -z "$(git -C "$PRODUCT_ROOT" status --porcelain -- . 2>/dev/null)" ] || dirty="-dirty"
		suffix="${suffix}-${hash}${dirty}"
	fi
	echo "$suffix"
}

LOCAL_APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# stage_local_app <dest>: <dest>/{backend,frontend} = this repo's core
# sources with the product copied in as backend/products/$PRODUCT and
# frontend/src/products/$PRODUCT (no node_modules, dist or venv).
stage_local_app() {
	local dest="$1"
	mkdir -p "${dest}/backend/products/${PRODUCT}" "${dest}/frontend/src/products/${PRODUCT}"
	rsync -a --exclude venv --exclude '__pycache__' "${LOCAL_APP_DIR}/backend/" "${dest}/backend/"
	rsync -a --exclude '__pycache__' "${PRODUCT_ROOT}/backend/" "${dest}/backend/products/${PRODUCT}/"
	rsync -a --exclude node_modules --exclude dist "${LOCAL_APP_DIR}/frontend/" "${dest}/frontend/"
	rsync -a "${PRODUCT_ROOT}/frontend/" "${dest}/frontend/src/products/${PRODUCT}/"
}

# build_release_tree <build_dir> <release_dir>: builds the frontend inside
# <build_dir> (from stage_local_app) and assembles the on-device layout
# (backend/, frontend/, PRODUCT) in <release_dir>. VERSION is the caller's.
build_release_tree() {
	local build="$1" release="$2"
	echo "==> Building the frontend (Vue) for product '${PRODUCT}'"
	( cd "${build}/frontend" && npm ci && KIOSK_PRODUCT="$PRODUCT" npm run build )
	mkdir -p "${release}/backend" "${release}/frontend"
	rsync -a --exclude 'test_*.py' "${build}/backend/" "${release}/backend/"
	rsync -a "${build}/frontend/dist/" "${release}/frontend/"
	echo "$PRODUCT" > "${release}/PRODUCT"
}
