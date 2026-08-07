#!/bin/bash
# Top-level entrypoint: builds the Slide Announcer Raspberry Pi image.
#
#   ./build.sh                 normal build (no SSH; a random per-build
#                               console-login password is still printed —
#                               see image-builder/README.md)
#   SSH_DEV_BUILD=1 ./build.sh same, plus SSH enabled with that same password
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

cleanup() {
	# pi-gen is a git submodule (its own repo) — our .gitignore can't reach
	# into it, so this staged copy must be removed explicitly rather than
	# left as untracked clutter in someone else's working tree.
	rm -rf "${PI_GEN_DIR}/stage-slide-announcer"
	[ -n "$WORK" ] && rm -rf "$WORK"
}
trap cleanup EXIT

echo "==> Ensuring pi-gen submodule is checked out"
git -C "$REPO_ROOT" submodule update --init "image-builder/pi-gen"

echo "==> Staging system/, provisioning/, local-app/ into the pi-gen custom stage"
FILES_DIR="${STAGE_SRC}/01-system-files/files"
rm -rf "$FILES_DIR"
mkdir -p "$FILES_DIR"
rsync -a --exclude 'backend/venv' "${REPO_ROOT}/system/" "${FILES_DIR}/system/"
rsync -a "${REPO_ROOT}/provisioning/" "${FILES_DIR}/provisioning/"
rsync -a --exclude 'backend/venv' "${REPO_ROOT}/local-app/" "${FILES_DIR}/local-app/"

# Build provenance: <kernel-version>-<build-date>-<git-hash> (kernel version
# is filled in by 01-system-files/00-run.sh, from inside the built rootfs —
# `uname -r` here would only tell us this x86 build host's kernel, not the
# image's, since the build is cross-compiled under qemu rather than booted).
BUILD_DATE="$(date -u +%Y-%m-%d)"
GIT_HASH="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
# `git diff --quiet` alone would miss untracked files — and right now
# almost everything here IS untracked, pre-commit. status --porcelain
# catches untracked/staged/unstaged all at once.
[ -z "$(git -C "$REPO_ROOT" status --porcelain)" ] || GIT_HASH="${GIT_HASH}-dirty"
{
	echo "BUILD_DATE=${BUILD_DATE}"
	echo "GIT_HASH=${GIT_HASH}"
} > "${FILES_DIR}/BUILD_INFO"
echo "==> Build provenance: date=${BUILD_DATE} git=${GIT_HASH}"

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

mkdir -p "$DEPLOY_DIR"
FINAL_IMG="${WORK}/${IMG_NAME}.img"
echo "==> Repartitioning into boot/rootA/rootB/data (requires root)"
sudo "${HERE}/repartition.sh" "${WORK}/raw.img" "$FINAL_IMG"

OUT_NAME="${IMG_NAME}-${BUILD_DATE}-${GIT_HASH}.img.xz"
echo "==> Compressing final image"
xz -6 -T0 -c "$FINAL_IMG" > "${DEPLOY_DIR}/${OUT_NAME}"
sudo chown "$(id -u):$(id -g)" "${DEPLOY_DIR}/${OUT_NAME}"

echo "==> Done: ${DEPLOY_DIR}/${OUT_NAME}"
