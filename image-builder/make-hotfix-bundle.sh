#!/bin/bash
# Packages a small, ad hoc RAUC bundle that patches files onto a device's
# CURRENTLY BOOTED root filesystem in place — no A/B slot, no reimage, no
# OTA rollout across rootA/rootB. For surgical one-off fixes (a handful of
# files, a config tweak) where a full image rebuild is overkill. This
# deliberately bypasses RAUC's A/B safety net (see system/rauc/system.conf's
# [slot.hotfix.0]): there is no fallback slot if a hotfix is wrong, unlike
# a real OS OTA. Don't use this for anything you'd want automatic rollback
# on.
#
#   ./make-hotfix-bundle.sh <files-dir> <required-version> <new-version>
#
# Output filename encodes both versions:
# slideannouncer-<new-version>.hotfix.from.<required-version>.raucb —
# no separate free-text label, since the versions already say what a
# human needs to know: what this hotfix requires and what it bumps to.
#
# <files-dir> is copied verbatim onto the device's root — e.g. a file at
# <files-dir>/opt/slide-announcer/foo.py lands at /opt/slide-announcer/foo.py
# on the device. The on-device hook writes through a non-recursive bind
# mount of / (see below) rather than the live / path directly, so this
# reaches the REAL underlying rootfs even for /etc and /var — which are
# normally shadowed there by this image's read-only-root overlays (see
# stage-slide-announcer/01-system-files/00-run.sh's comment) — instead of
# just adding another ephemeral/tmpfs (/var) or /data-backed (/etc) upper
# layer on top, the way a plain live file push would.
#
# <required-version>/<new-version> guard against applying a surgical,
# untested-by-RAUC's-own-A/B-safety-net fix to the wrong base image, and
# against reapplying the same hotfix twice: the on-device hook refuses to
# touch anything unless /opt/slide-announcer/VERSION currently reads
# exactly <required-version>, and overwrites it with <new-version> only
# after the file drop succeeds — so a device that already has this hotfix
# (or was never at the version it targets) just rejects it instead of
# re-patching or drifting from what a `rauc status`/heartbeat report
# actually says its version is.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="${HERE}/deploy"

USAGE="usage: $0 <files-dir> <required-version> <new-version>"
FILES_DIR="${1:?$USAGE}"
REQUIRED_VERSION="${2:?$USAGE}"
NEW_VERSION="${3:?$USAGE}"

if [ ! -d "$FILES_DIR" ]; then
	echo "make-hotfix-bundle.sh: ${FILES_DIR} is not a directory" >&2
	exit 1
fi

# Same cert/key loading as build.sh — see its own comment for the
# dev-vs-production distinction.
if [ -f "${HERE}/.env" ]; then
	set -a
	# shellcheck disable=SC1091
	. "${HERE}/.env"
	set +a
fi
RAUC_CERT_PATH="${RAUC_CERT_PATH:-}"
RAUC_KEY_PATH="${RAUC_KEY_PATH:-}"
if [ -z "$RAUC_CERT_PATH" ] || [ -z "$RAUC_KEY_PATH" ] \
	|| [ ! -f "$RAUC_CERT_PATH" ] || [ ! -f "$RAUC_KEY_PATH" ]; then
	echo "make-hotfix-bundle.sh: RAUC_CERT_PATH/RAUC_KEY_PATH not set to existing files (see image-builder/.env)" >&2
	exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

BUNDLE_DIR="${WORK}/bundle"
mkdir -p "$BUNDLE_DIR"
tar -C "$FILES_DIR" -czf "${BUNDLE_DIR}/files.tar.gz" .

# hooks=install fully overrides RAUC's default install for this slot (same
# pattern as the real kernel slots in build.sh) — remount / rw only for the
# duration of the extraction, never leave it that way. Version check runs
# BEFORE the remount/extract, so a mismatch leaves the device completely
# untouched rather than partially patched. Placeholder tokens (not direct
# $REQUIRED_VERSION/$NEW_VERSION interpolation) because this heredoc is
# quoted — deliberately, so the *runtime* variables below (RAUC_IMAGE_NAME)
# aren't expanded now, at bundle-build time, instead of later on-device.
cat > "${BUNDLE_DIR}/hook.sh" <<'HOOKEOF'
#!/bin/bash
set -euo pipefail
case "${1:-}" in
slot-install)
	CURRENT_VERSION="$(cat /opt/slide-announcer/VERSION 2>/dev/null || echo '')"
	if [ "$CURRENT_VERSION" != "@@REQUIRED_VERSION@@" ]; then
		echo "hotfix: expected /opt/slide-announcer/VERSION='@@REQUIRED_VERSION@@', found '${CURRENT_VERSION}' — refusing to apply" >&2
		exit 1
	fi
	mount -o remount,rw /
	# Always try to leave / read-only again, even if extraction below
	# fails partway — a hook error already fails the whole RAUC install
	# (see build.sh's kernel-slot hook for the same class of failure); no
	# reason to also leave the device stuck read-write until next reboot.
	trap 'umount /mnt/root 2>/dev/null || true; mount -o remount,ro / 2>/dev/null || true' EXIT
	mkdir -p /mnt/root
	# Non-recursive --bind (NOT --rbind) deliberately: it does not carry
	# /etc's and /var's overlay submounts along with it, so
	# /mnt/root/etc and /mnt/root/var are the real, underlying rootfs
	# directories those overlays otherwise shadow at the live /etc, /var
	# paths — writing through here lands in the base image itself, not an
	# ephemeral tmpfs (/var) or /data-backed (/etc) upper layer that a
	# reboot or factory reset would otherwise undo.
	mount --bind / /mnt/root
	tar -xzf "${RAUC_IMAGE_NAME:?}" -C /mnt/root
	echo "@@NEW_VERSION@@" > /mnt/root/opt/slide-announcer/VERSION
	;;
esac
HOOKEOF
sed -i "s/@@REQUIRED_VERSION@@/${REQUIRED_VERSION}/g; s/@@NEW_VERSION@@/${NEW_VERSION}/g" "${BUNDLE_DIR}/hook.sh"
chmod +x "${BUNDLE_DIR}/hook.sh"

cat > "${BUNDLE_DIR}/manifest.raucm" <<EOF
[update]
compatible=slideannouncer-rpi4
version=${NEW_VERSION}

# verity, not plain — same reasoning as build.sh's main bundle: an
# install-from-URL streams over HTTP, and plain-format bundles can't be
# authenticated block-by-block during a stream.
[bundle]
format=verity

[hooks]
filename=hook.sh

[image.hotfix]
filename=files.tar.gz
hooks=install
EOF

mkdir -p "$DEPLOY_DIR"
BUNDLE_OUT="${DEPLOY_DIR}/slideannouncer-${NEW_VERSION}.hotfix.from.${REQUIRED_VERSION}.raucb"
echo "==> Building and signing hotfix bundle"
rauc bundle --cert="$RAUC_CERT_PATH" --key="$RAUC_KEY_PATH" "$BUNDLE_DIR" "$BUNDLE_OUT"

echo "==> Done: ${BUNDLE_OUT}"
echo "==> Install on-device with: rauc install <url-to-this-file>"
