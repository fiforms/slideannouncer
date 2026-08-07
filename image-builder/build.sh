#!/bin/bash
# Top-level entrypoint: builds the Slide Announcer Raspberry Pi image.
#
#   ./build.sh                 normal build (no SSH; a random per-build
#                               console-login password is still printed —
#                               see image-builder/README.md)
#   SSH_DEV_BUILD=1 ./build.sh same, plus SSH enabled with that same password
#   RESUME_WORK=<dir> ./build.sh
#                               skip the pi-gen/Docker build and reuse an
#                               already-decompressed raw.img from a previous
#                               run's WORK dir (printed on failure below) —
#                               for retrying repartition.sh onward without
#                               paying for the slow stage again
#
# Pipeline: stage our files into pi-gen -> run pi-gen via Docker -> take its
# raw boot+root .img output -> repartition.sh into the final
# boot/rootA/rootB/data layout -> compress -> image-builder/deploy/.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/.." && pwd)"
PI_GEN_DIR="${HERE}/pi-gen"
STAGE_SRC="${HERE}/stage-slide-announcer"
DEPLOY_DIR="${HERE}/deploy"
IMG_NAME="slideannouncer"
WORK=""
RAW_IMG_READY=0
SUDO_KEEPALIVE_PID=""

# RAUC bundle signing cert/key, from image-builder/.env (never committed —
# see .gitignore). Checked up front, before the sudo prompt and the long
# pi-gen build, so a missing pair fails fast and cheap instead of after
# 20+ minutes of building an image with nowhere to sign the bundle.
if [ -f "${HERE}/.env" ]; then
	set -a
	# shellcheck disable=SC1091
	. "${HERE}/.env"
	set +a
fi
RAUC_CERT_PATH="${RAUC_CERT_PATH:-}"
RAUC_KEY_PATH="${RAUC_KEY_PATH:-}"
# Defaults to RAUC_CERT_PATH — correct for a single dev/test cert doing
# both jobs. A production PKI (generate-rauc-cert.sh production) sets this
# to the CA cert instead, separate from the signing cert used below, so
# devices trust the CA rather than one specific rotatable signing cert.
RAUC_KEYRING_CERT_PATH="${RAUC_KEYRING_CERT_PATH:-$RAUC_CERT_PATH}"
if [ -z "$RAUC_CERT_PATH" ] || [ -z "$RAUC_KEY_PATH" ] \
	|| [ ! -f "$RAUC_CERT_PATH" ] || [ ! -f "$RAUC_KEY_PATH" ] \
	|| [ ! -f "$RAUC_KEYRING_CERT_PATH" ]; then
	cat >&2 <<EOF
build.sh: RAUC_CERT_PATH/RAUC_KEY_PATH (/RAUC_KEYRING_CERT_PATH) not set to existing files.

This build produces two artifacts: the raw .img.xz (initial provisioning)
and a signed .raucb bundle (OTA updates) — the bundle needs a signing
cert/key pair to exist before the build starts.

No pair yet?
    ./generate-rauc-cert.sh dev          # throwaway, dev/test only
    ./generate-rauc-cert.sh production   # offline CA + rotatable signing cert

Then copy .env.example to .env and set RAUC_CERT_PATH/RAUC_KEY_PATH (and
RAUC_KEYRING_CERT_PATH, for production) to the paths it prints.
EOF
	exit 1
fi
if ! command -v rauc >/dev/null 2>&1; then
	echo "build.sh: 'rauc' not found on this build host (needed to bundle/sign the .raucb) — install the rauc package" >&2
	exit 1
fi

cleanup() {
	local exit_code=$?
	[ -n "$SUDO_KEEPALIVE_PID" ] && kill "$SUDO_KEEPALIVE_PID" 2>/dev/null
	# pi-gen is a git submodule (its own repo) — our .gitignore can't reach
	# into it, so this staged copy must be removed explicitly rather than
	# left as untracked clutter in someone else's working tree.
	rm -rf "${PI_GEN_DIR}/stage-slide-announcer"
	if [ -n "$WORK" ]; then
		if [ "$exit_code" != 0 ] && [ "$RAW_IMG_READY" = 1 ]; then
			echo "==> Failed after raw.img was ready — kept it for a retry:" >&2
			echo "      RESUME_WORK=${WORK} $0" >&2
			echo "    (skips re-running pi-gen/Docker, resumes at repartition.sh)" >&2
		else
			rm -rf "$WORK"
		fi
	fi
	exit "$exit_code"
}
trap cleanup EXIT

RESUME_WORK="${RESUME_WORK:-}"
if [ -n "$RESUME_WORK" ]; then
	WORK="$RESUME_WORK"
	if [ ! -f "${WORK}/raw.img" ]; then
		echo "RESUME_WORK=${WORK} has no raw.img to resume from" >&2
		exit 1
	fi
	echo "==> RESUME_WORK=${WORK}: reusing existing raw.img, skipping pi-gen/Docker build"
fi

# repartition.sh (below) needs root, but only after the pi-gen/Docker build —
# which can run long enough that a sudo timestamp grabbed only right before
# that call has since expired, with no one at the keyboard to re-enter a
# password. Authenticate up front instead, and keep the ticket alive for the
# whole build so the later `sudo repartition.sh` never has to prompt again.
echo "==> repartition.sh will need root partway through this build"
if [ -t 0 ]; then
	read -n 1 -s -r -p "    Press any key to authenticate sudo now, before the long pi-gen build... "
	echo
fi
sudo -v
(
	while kill -0 "$$" 2>/dev/null; do
		sudo -n true 2>/dev/null
		sleep 60
	done
) &
SUDO_KEEPALIVE_PID=$!

# Build provenance: <kernel-version>-<build-date>-<git-hash> (kernel version
# is filled in by 01-system-files/00-run.sh, from inside the built rootfs —
# `uname -r` here would only tell us this x86 build host's kernel, not the
# image's, since the build is cross-compiled under qemu rather than booted).
# Computed unconditionally (even on RESUME_WORK) since it's also used below
# for the final output filename.
BUILD_DATE="$(date -u +%Y-%m-%d)"
GIT_HASH="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
# `git diff --quiet` alone would miss untracked files — and right now
# almost everything here IS untracked, pre-commit. status --porcelain
# catches untracked/staged/unstaged all at once.
[ -z "$(git -C "$REPO_ROOT" status --porcelain)" ] || GIT_HASH="${GIT_HASH}-dirty"

if [ -n "$RESUME_WORK" ]; then
	RAW_IMG_READY=1
else

echo "==> Ensuring pi-gen submodule is checked out"
git -C "$REPO_ROOT" submodule update --init "image-builder/pi-gen"

echo "==> Staging system/, provisioning/, local-app/ into the pi-gen custom stage"
FILES_DIR="${STAGE_SRC}/01-system-files/files"
rm -rf "$FILES_DIR"
mkdir -p "$FILES_DIR"
rsync -a --exclude 'backend/venv' "${REPO_ROOT}/system/" "${FILES_DIR}/system/"
rsync -a "${REPO_ROOT}/provisioning/" "${FILES_DIR}/provisioning/"
rsync -a --exclude 'backend/venv' "${REPO_ROOT}/local-app/" "${FILES_DIR}/local-app/"

{
	echo "BUILD_DATE=${BUILD_DATE}"
	echo "GIT_HASH=${GIT_HASH}"
} > "${FILES_DIR}/BUILD_INFO"
echo "==> Build provenance: date=${BUILD_DATE} git=${GIT_HASH}"

# Only a public cert goes into the image's RAUC keyring (installed by
# 00-run.sh) — RAUC_KEYRING_CERT_PATH, not RAUC_CERT_PATH (the two are the
# same file in dev mode, but the CA cert vs. the signing cert in
# production mode — see .env.example). No private key ever gets staged
# here; it's only ever passed as an argument to `rauc bundle` below.
cp "$RAUC_KEYRING_CERT_PATH" "${FILES_DIR}/rauc-keyring.pem"

echo "==> Copying the custom stage into pi-gen (Docker build context = pi-gen/ only)"
rsync -a --delete "${STAGE_SRC}/" "${PI_GEN_DIR}/stage-slide-announcer/"

# A local account always gets created with a random per-build password
# (never just deferred to Raspberry Pi OS's interactive first-boot wizard —
# with no FIRST_USER_PASS set, that wizard demands a keyboard-driven
# keyboard-layout-then-create-account flow on every fresh card, which is
# exactly the console-hijacking behavior the kiosk exists to avoid).
# DISABLE_FIRST_BOOT_USER_RENAME=1 requires FIRST_USER_PASS to be set (pi-gen
# enforces this at build time) — that's what makes the account exist non-
# interactively instead of via the wizard. This is a real local login,
# useful for field debugging with a physical keyboard; it's just not one
# that's ever exposed remotely unless SSH is also enabled below.
# Not `tr -dc ... < /dev/urandom | head -c 16` — /dev/urandom is an
# infinite stream, so `head -c` closes it early and SIGPIPEs `tr`, which
# `set -o pipefail` below treats as a pipeline failure (silently, since
# SIGPIPE prints nothing) and `set -e` then kills the whole script. Bound
# the read at the source instead, and slice in bash rather than with
# another `head -c` (same trap one stage later otherwise).
USER_PASS_RAW="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
USER_PASS="${USER_PASS_RAW:0:16}"
CONFIG_FILE="$(mktemp)"
cat "${HERE}/config" > "$CONFIG_FILE"
{
	echo "DISABLE_FIRST_BOOT_USER_RENAME=1"
	echo "FIRST_USER_PASS=${USER_PASS}"
} >> "$CONFIG_FILE"
echo "==> Local account 'slideadmin' password (console/keyboard login only): ${USER_PASS}"

if [ "${SSH_DEV_BUILD:-0}" = "1" ]; then
	echo "ENABLE_SSH=1" >> "$CONFIG_FILE"
	echo "==> SSH_DEV_BUILD=1: SSH enabled too — same password as above"
fi

if ! command -v qemu-aarch64-static >/dev/null 2>&1; then
	# Newer Debian/Ubuntu ship the (still statically linked) interpreter as
	# just `qemu-aarch64`, dropping the `-static` suffix — but pi-gen's
	# build-docker.sh looks for that exact name on the host to register
	# binfmt_misc. Symlink it in (no sudo needed — ~/.local/bin, added to
	# PATH for this script only) rather than requiring a manual host tweak.
	QEMU_AARCH64_BIN="$(command -v qemu-aarch64 || true)"
	if [ -n "$QEMU_AARCH64_BIN" ]; then
		mkdir -p "${HOME}/.local/bin"
		ln -sf "$QEMU_AARCH64_BIN" "${HOME}/.local/bin/qemu-aarch64-static"
		export PATH="${HOME}/.local/bin:${PATH}"
		echo "==> Linked ${QEMU_AARCH64_BIN} as ${HOME}/.local/bin/qemu-aarch64-static"
	else
		echo "qemu-aarch64(-static) not found — install qemu-user-binfmt (Ubuntu) or qemu-user-static (Debian)" >&2
		exit 1
	fi
fi

echo "==> Running pi-gen (Docker)"
# build-docker.sh writes deploy/ and build-docker.log relative to the
# caller's cwd, not its own location — must run from inside pi-gen/.
(cd "$PI_GEN_DIR" && ./build-docker.sh -c "$CONFIG_FILE")

echo "==> Locating pi-gen's raw image output"
RAW_XZ="$(find "${PI_GEN_DIR}/deploy" -maxdepth 1 -name "*${IMG_NAME}*.img.xz" ! -name '*-lite*' | head -n1)"
if [ -z "$RAW_XZ" ]; then
	echo "Could not find pi-gen output image in ${PI_GEN_DIR}/deploy" >&2
	exit 1
fi

WORK="$(mktemp -d)"
echo "==> Decompressing $(basename "$RAW_XZ")"
unxz -k -c "$RAW_XZ" > "${WORK}/raw.img"
RAW_IMG_READY=1

fi

mkdir -p "$DEPLOY_DIR"
FINAL_IMG="${WORK}/${IMG_NAME}.img"
echo "==> Repartitioning into boot/rootA/rootB/data (requires root)"
sudo "${HERE}/repartition.sh" "${WORK}/raw.img" "$FINAL_IMG"

OUT_NAME="${IMG_NAME}-${BUILD_DATE}-${GIT_HASH}.img.xz"
echo "==> Compressing final image"
xz -6 -T0 -c "$FINAL_IMG" > "${DEPLOY_DIR}/${OUT_NAME}"
sudo chown "$(id -u):$(id -g)" "${DEPLOY_DIR}/${OUT_NAME}"

echo "==> Done: ${DEPLOY_DIR}/${OUT_NAME}"

# --- RAUC bundle: same rootA content, packaged as a signed .raucb for OTA
# (the .img.xz above is the whole boot+rootA+rootB+data disk, for initial
# flashing only — RAUC bundles a single slot's filesystem image, not a
# disk) -----------------------------------------------------------------
echo "==> Extracting rootA into a standalone image for RAUC bundling"
BUNDLE_DIR="${WORK}/bundle"
mkdir -p "$BUNDLE_DIR"
FINAL_LOOP="$(sudo losetup --show --find --partscan --read-only "$FINAL_IMG")"
udevadm settle 2>/dev/null || true
sudo dd if="${FINAL_LOOP}p2" of="${BUNDLE_DIR}/rootfs.img" bs=4M status=none
sudo losetup -d "$FINAL_LOOP"
sudo chown "$(id -u):$(id -g)" "${BUNDLE_DIR}/rootfs.img"

# Read the exact version stamp 00-run.sh wrote into the image itself
# (kernel-version-build_date-git_hash) rather than reconstructing it here,
# so the RAUC manifest's version always matches what a running device
# reports via /opt/slide-announcer/VERSION.
ROOTFS_MNT="${WORK}/rootfs-mnt"
mkdir -p "$ROOTFS_MNT"
sudo mount -o ro,loop "${BUNDLE_DIR}/rootfs.img" "$ROOTFS_MNT"
IMAGE_VERSION="$(cat "${ROOTFS_MNT}/opt/slide-announcer/VERSION")"
sudo umount "$ROOTFS_MNT"

cat > "${BUNDLE_DIR}/manifest.raucm" <<EOF
[update]
compatible=slideannouncer-rpi4
version=${IMAGE_VERSION}

[bundle]
format=plain

[image.rootfs]
filename=rootfs.img
EOF

BUNDLE_OUT="${DEPLOY_DIR}/${IMG_NAME}-${BUILD_DATE}-${GIT_HASH}.raucb"
echo "==> Building and signing RAUC bundle"
rauc bundle --cert="$RAUC_CERT_PATH" --key="$RAUC_KEY_PATH" "$BUNDLE_DIR" "$BUNDLE_OUT"

echo "==> Done: ${BUNDLE_OUT}"
