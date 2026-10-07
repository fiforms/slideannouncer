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
