#!/bin/bash
# Builds the Vue frontend and packages backend+frontend+VERSION into a
# release tarball — the artifact both image-builder/build.sh (embeds it in
# the OS image, extracted on-device by system/scripts/local-app-seed.py)
# and the future updater/ (downloads it directly over HTTP) consume. See
# README.md's "Installation on the device" section for the full design.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="${HERE}/deploy"

VERSION_BASE="$(cat "${HERE}/VERSION")"
GIT_HASH="$(git -C "$HERE" rev-parse --short HEAD)"
# `git diff --quiet` alone would miss untracked files — status --porcelain
# catches untracked/staged/unstaged all at once (same check as
# image-builder/build.sh's own version stamp).
[ -z "$(git -C "$HERE" status --porcelain)" ] || GIT_HASH="${GIT_HASH}-dirty"
VERSION="${VERSION_BASE}-${GIT_HASH}"

echo "==> Building local-app/frontend (Vue)"
( cd "${HERE}/frontend" && npm ci && npm run build )

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "${STAGE}/backend" "${STAGE}/frontend"
rsync -a --exclude venv --exclude '__pycache__' "${HERE}/backend/" "${STAGE}/backend/"
rsync -a "${HERE}/frontend/dist/" "${STAGE}/frontend/"
# Read by the (not yet built) updater to compare against a candidate
# download, and by system/scripts/local-app-seed.py to decide whether the
# image's embedded release is newer than what's already on /data. Only the
# leading X.Y.Z is ever compared — see that script's version_core() for why
# a rebuild without bumping VERSION is deliberately not treated as newer.
echo "$VERSION" > "${STAGE}/VERSION"

mkdir -p "$DEPLOY_DIR"
OUT_TARBALL="${DEPLOY_DIR}/slide-announcer-local-app-${VERSION}.tar.gz"
tar -czf "$OUT_TARBALL" -C "$STAGE" .

echo "$VERSION" > "${DEPLOY_DIR}/VERSION"
ln -sf "$(basename "$OUT_TARBALL")" "${DEPLOY_DIR}/slide-announcer-local-app-latest.tar.gz"

echo "==> Built ${OUT_TARBALL}"
