#!/bin/bash
# Pushes a live local-app build straight onto a real device's /data over
# SSH, bypassing package.sh's release tarball and the OTA updater entirely
# — for fast iteration against real hardware instead of a full
# reimage/hotfix per change. Nothing here is meant to ship anywhere; it
# just drives the same /data/local-app/current symlink-swap mechanism
# local-app-seed.py and the (future) updater already use — see
# README.md's "Installation on the device."
#
#   ./dev-deploy.sh <user@host> [--restart-kiosk]
#
# <user@host> — e.g. slideadmin@slideannouncer.local. slideadmin is in the
# `slideannouncer` group (00-run.sh's usermod), and
# /data/local-app/{,releases} are group-writable 0775 (local-app-seed.py),
# so pushing a new release here needs no root. Restarting
# slide-announcer-backend.service (and, with --restart-kiosk,
# slide-announcer-kiosk.service) needs no sudo password either — polkit
# already authorizes slideadmin for any slide-announcer-*.service action
# (system/polkit/50-slide-announcer-system.rules).
#
# Frontend changes are live immediately without --restart-kiosk: nginx
# serves straight from the `current` symlink at request time, so reloading
# the kiosk page (or the browser tab on Settings/Pairing) is enough.
# Backend changes need the restart this script always does. --restart-kiosk
# is only for forcing Chromium itself to reload without touching it by hand.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

USAGE="usage: $0 <user@host> [--restart-kiosk]"
TARGET="${1:?$USAGE}"
RESTART_KIOSK=false
if [ "${2:-}" = "--restart-kiosk" ]; then
	RESTART_KIOSK=true
fi

echo "==> Building local-app/frontend (Vue)"
( cd "${HERE}/frontend" && npm ci && npm run build )

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "${STAGE}/backend" "${STAGE}/frontend"
rsync -a --exclude venv --exclude '__pycache__' "${HERE}/backend/" "${STAGE}/backend/"
rsync -a "${HERE}/frontend/dist/" "${STAGE}/frontend/"
# Distinct from any real release's X.Y.Z-<hash> shape (see package.sh) so
# it's never mistaken for one — this is a throwaway dev push, not
# something local-app-seed.py's version comparison should ever reason
# about (it only runs at boot, against the OS image's embedded release,
# and never touches releases/dev at all).
echo "dev-$(date +%Y%m%d-%H%M%S)" > "${STAGE}/VERSION"

echo "==> Pushing to ${TARGET}:/data/local-app/releases/dev"
ssh "$TARGET" "mkdir -p /data/local-app/releases/dev"
rsync -a --delete "${STAGE}/" "${TARGET}:/data/local-app/releases/dev/"

echo "==> Swapping current -> releases/dev and restarting the backend"
ssh "$TARGET" '
	set -euo pipefail
	chmod -R u+rwX,go+rX /data/local-app/releases/dev
	ln -sfn /data/local-app/releases/dev /data/local-app/.current.tmp
	mv -T /data/local-app/.current.tmp /data/local-app/current
	systemctl restart slide-announcer-backend.service
'

if $RESTART_KIOSK; then
	echo "==> Restarting kiosk display"
	ssh "$TARGET" "systemctl restart slide-announcer-kiosk.service"
else
	echo "==> Frontend is live immediately — reload the kiosk/Settings page, or rerun with --restart-kiosk to relaunch Chromium."
fi

echo "==> Done. ${TARGET}:/data/local-app/current -> releases/dev"
