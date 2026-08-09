#!/bin/bash
# Top-level entrypoint: builds the Slide Announcer Raspberry Pi image.
#
#   ./build.sh                 normal build (no SSH; a random per-build
#                               console-login password is still printed —
#                               see image-builder/README.md)
#   SSH_DEV_BUILD=1 ./build.sh same, plus SSH enabled with that same password
#                               (a dev convenience — password auth stays on;
#                               for a real fleet image, use
#                               SLIDE_ANNOUNCER_ENABLE_SSH/
#                               SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH in .env
#                               instead, see .env.example)
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
if ! command -v npm >/dev/null 2>&1; then
	echo "build.sh: 'npm' not found on this build host (needed to build local-app/frontend's Vue app) — install Node.js" >&2
	exit 1
fi

# The AnnouncementSlides server this fleet talks to (pairing, sync,
# heartbeat, and — relevant here — where the future OTA-check unit polls
# for release info). One self-hosted server per fleet, so this is a
# build-time constant baked into every image rather than a per-device
# setting like device_uuid. Validated up front for the same reason as the
# RAUC cert/key above: fail before the expensive pi-gen build, not after.
SLIDE_ANNOUNCER_SERVER_URL="${SLIDE_ANNOUNCER_SERVER_URL:-}"
if [ -z "$SLIDE_ANNOUNCER_SERVER_URL" ]; then
	cat >&2 <<EOF
build.sh: SLIDE_ANNOUNCER_SERVER_URL is not set.

Every device built from this image needs to know which AnnouncementSlides
server to talk to (pairing/sync/heartbeat, and OTA update checks).

Copy .env.example to .env (if you haven't already) and set:
    SLIDE_ANNOUNCER_SERVER_URL=https://your-server.example.org
EOF
	exit 1
fi
case "$SLIDE_ANNOUNCER_SERVER_URL" in
	https://*/|http://*/)
		echo "build.sh: SLIDE_ANNOUNCER_SERVER_URL should not have a trailing slash: ${SLIDE_ANNOUNCER_SERVER_URL}" >&2
		exit 1
		;;
	https://?*|http://?*) ;;
	*)
		echo "build.sh: SLIDE_ANNOUNCER_SERVER_URL doesn't look like a URL (expected http(s)://...): ${SLIDE_ANNOUNCER_SERVER_URL}" >&2
		exit 1
		;;
esac

# WiFi regulatory domain (ISO 3166-1 alpha-2, e.g. US) — the Pi's WiFi
# radio ships soft rfkill-blocked until this is set (a kernel/cfg80211
# requirement, not something nmcli/NetworkManager can work around from the
# device side), so without this every device would need someone at the
# console running raspi-config by hand before Settings > Network's WiFi
# scan could ever see anything. Optional, defaults to US — this fleet's
# deployment target — since getting a device on WiFi at all matters more
# here than failing the build over a missing regulatory code.
SLIDE_ANNOUNCER_WIFI_COUNTRY="${SLIDE_ANNOUNCER_WIFI_COUNTRY:-US}"
if ! [[ "$SLIDE_ANNOUNCER_WIFI_COUNTRY" =~ ^[A-Z]{2}$ ]]; then
	echo "build.sh: SLIDE_ANNOUNCER_WIFI_COUNTRY must be a 2-letter ISO 3166-1 code (e.g. US), got: ${SLIDE_ANNOUNCER_WIFI_COUNTRY}" >&2
	exit 1
fi
echo "==> WiFi regulatory domain: ${SLIDE_ANNOUNCER_WIFI_COUNTRY}"

# Extra config.txt lines for hardware this fleet needs that a stock image
# doesn't set — e.g. a fan control overlay. Optional, free-form (one or
# more raw config.txt lines — see .env.example), appended under their own
# [all] section by 00-run.sh. Not validated here beyond "not accidentally
# a [section] header" — config.txt's own parser is the real validator, and
# a typo here should fail obviously at boot (dmesg/dtoverlay warnings),
# not silently.
SLIDE_ANNOUNCER_BOOT_CONFIG_EXTRA="${SLIDE_ANNOUNCER_BOOT_CONFIG_EXTRA:-}"
if [[ "$SLIDE_ANNOUNCER_BOOT_CONFIG_EXTRA" == *"["* ]]; then
	echo "build.sh: SLIDE_ANNOUNCER_BOOT_CONFIG_EXTRA shouldn't include a [section] header — it's already placed under its own [all] section" >&2
	exit 1
fi
if [ -n "$SLIDE_ANNOUNCER_BOOT_CONFIG_EXTRA" ]; then
	echo "==> Extra config.txt lines: ${SLIDE_ANNOUNCER_BOOT_CONFIG_EXTRA}"
fi

# SSH is off by default (see ENABLE_SSH=0 in ./config). It only turns on
# when BOTH of these are set — a bare "enable SSH" flag with no key would
# mean password auth over the network with no way to lock that down, and a
# bare key with no flag would be silently inert, so neither alone does
# anything. Once both are set, password authentication over SSH is
# disabled globally (PUBKEY_ONLY_SSH below) — key-based login is the only
# way in, on top of the normal slideadmin console password.
SLIDE_ANNOUNCER_ENABLE_SSH="${SLIDE_ANNOUNCER_ENABLE_SSH:-0}"
SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH="${SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH:-}"
SSH_ENABLED=0
if [ "$SLIDE_ANNOUNCER_ENABLE_SSH" = "1" ] && [ -n "$SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH" ]; then
	if [ ! -f "$SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH" ]; then
		echo "build.sh: SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH is not a file: ${SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH}" >&2
		exit 1
	fi
	SSH_PUBLIC_KEY_CONTENT="$(cat "$SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH")"
	case "$SSH_PUBLIC_KEY_CONTENT" in
		ssh-rsa\ *|ssh-ed25519\ *|ecdsa-sha2-*\ *) ;;
		*)
			echo "build.sh: SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH doesn't look like an SSH public key (expected it to start with ssh-rsa/ssh-ed25519/ecdsa-sha2-...): ${SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH}" >&2
			exit 1
			;;
	esac
	SSH_ENABLED=1
	echo "==> SSH enabled for slideadmin (key-based only, from ${SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH}) — password authentication disabled globally"
elif [ "$SLIDE_ANNOUNCER_ENABLE_SSH" = "1" ] || [ -n "$SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH" ]; then
	echo "==> Only one of SLIDE_ANNOUNCER_ENABLE_SSH/SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH is set — both are required, so SSH stays disabled" >&2
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

echo "==> Building the local-app release tarball (local-app/package.sh)"
"${REPO_ROOT}/local-app/package.sh"
LOCAL_APP_VERSION="$(cat "${REPO_ROOT}/local-app/deploy/VERSION")"
echo "==> local-app version: ${LOCAL_APP_VERSION}"

echo "==> Staging system/, provisioning/, local-app/ into the pi-gen custom stage"
FILES_DIR="${STAGE_SRC}/01-system-files/files"
rm -rf "$FILES_DIR"
mkdir -p "$FILES_DIR"
rsync -a --exclude 'backend/venv' "${REPO_ROOT}/system/" "${FILES_DIR}/system/"
rsync -a "${REPO_ROOT}/provisioning/" "${FILES_DIR}/provisioning/"
# The device gets no local-app *source* at all — only the built release
# tarball, baked in read-only at a fixed rootfs path, plus requirements.txt
# (to build the venv, itself fixed OS-image infra independent of the app
# version). system/scripts/local-app-seed.py extracts the tarball onto
# /data on first boot (or after an OS update ships a newer app than what's
# already installed there) — see local-app/README.md's "Installation on
# the device" section for the full design.
install -d "${FILES_DIR}/local-app-release"
cp "${REPO_ROOT}/local-app/deploy/slide-announcer-local-app-latest.tar.gz" \
	"${FILES_DIR}/local-app-release/local-app.tar.gz"
cp "${REPO_ROOT}/local-app/deploy/VERSION" "${FILES_DIR}/local-app-release/VERSION"
cp "${REPO_ROOT}/local-app/backend/requirements.txt" "${FILES_DIR}/local-app-release/requirements.txt"

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

echo "$SLIDE_ANNOUNCER_SERVER_URL" > "${FILES_DIR}/SERVER_URL"
echo "$SLIDE_ANNOUNCER_WIFI_COUNTRY" > "${FILES_DIR}/WIFI_COUNTRY"
# Written even when empty (as a genuinely empty file, not just a blank
# line) — 00-run.sh checks `[ -s ... ]` (non-empty) so an absent/blank
# setting is just a no-op, no branching needed here.
: > "${FILES_DIR}/BOOT_CONFIG_EXTRA"
if [ -n "$SLIDE_ANNOUNCER_BOOT_CONFIG_EXTRA" ]; then
	printf '%s\n' "$SLIDE_ANNOUNCER_BOOT_CONFIG_EXTRA" > "${FILES_DIR}/BOOT_CONFIG_EXTRA"
fi

# Root password — debugging/development only (see .env.example). Left
# unset, root stays locked, same as a stock image. 00-run.sh's on_chroot
# block reads this file (if present) and chpasswd's root with it; never
# committed (files/ is gitignored) and never baked into a real fleet
# image unless someone deliberately sets ROOT_DEV_PASSWORD.
if [ -n "${ROOT_DEV_PASSWORD:-}" ]; then
	echo "$ROOT_DEV_PASSWORD" > "${FILES_DIR}/ROOT_DEV_PASSWORD"
	echo "==> ROOT_DEV_PASSWORD set: root login enabled for this build — debug/dev images only, never a real fleet image"
fi

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
if [ -n "${SLIDEADMIN_PASSWORD:-}" ]; then
	USER_PASS="$SLIDEADMIN_PASSWORD"
else
	USER_PASS_RAW="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
	USER_PASS="${USER_PASS_RAW:0:16}"
fi
CONFIG_FILE="$(mktemp)"
cat "${HERE}/config" > "$CONFIG_FILE"
{
	echo "DISABLE_FIRST_BOOT_USER_RENAME=1"
	echo "FIRST_USER_PASS=${USER_PASS}"
	# pi-gen's own stage2/02-net-tweaks/01-run.sh checks this var: set, it
	# calls raspi-config itself (redundant with, but harmless alongside,
	# our own call in stage-slide-announcer/01-system-files/00-run.sh);
	# UNSET, it instead bakes WirelessEnabled=false into
	# /var/lib/NetworkManager/NetworkManager.state — a *separate* NM-level
	# radio-off flag from the kernel rfkill block, and one that would
	# otherwise re-assert on every boot (that file lives on /var, which is
	# a tmpfs overlay reset every boot per the read-only-rootfs design, so
	# a live `nmcli radio wifi on` never survives a reboot without this).
	echo "WPA_COUNTRY=${SLIDE_ANNOUNCER_WIFI_COUNTRY}"
} >> "$CONFIG_FILE"
echo "==> Local account 'slideadmin' password (console/keyboard login only): ${USER_PASS}"

if [ "${SSH_DEV_BUILD:-0}" = "1" ]; then
	echo "ENABLE_SSH=1" >> "$CONFIG_FILE"
	echo "==> SSH_DEV_BUILD=1: SSH enabled too — same password as above"
fi

# Key-based SSH from .env (see the SLIDE_ANNOUNCER_ENABLE_SSH/
# SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH validation above). PUBKEY_ONLY_SSH is
# pi-gen's own knob for this — it rewrites /etc/ssh/sshd_config to disable
# PasswordAuthentication and enable PubkeyAuthentication, globally, so this
# takes precedence over SSH_DEV_BUILD's password-based access above (both
# can be set at once — password login just stops working either way).
# printf %q rather than a plain assignment: PUBKEY_SSH_FIRST_USER's value
# has spaces (`ssh-ed25519 AAAA... comment`), and this file gets sourced as
# a shell script by pi-gen's build.sh, so it needs to come out quoted.
if [ "$SSH_ENABLED" = "1" ]; then
	echo "ENABLE_SSH=1" >> "$CONFIG_FILE"
	echo "PUBKEY_ONLY_SSH=1" >> "$CONFIG_FILE"
	printf 'PUBKEY_SSH_FIRST_USER=%q\n' "$SSH_PUBLIC_KEY_CONTENT" >> "$CONFIG_FILE"
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

# --- RAUC bundle: rootA content + boot-partition files, packaged as a
# signed .raucb for OTA (the .img.xz above is the whole
# boot+rootA+rootB+data disk, for initial flashing only — a RAUC bundle
# carries per-slot-class images instead: "rootfs" here is a full ext4
# filesystem image, "kernel" is a tarball of the boot-partition files a
# custom-slot hook unpacks into slotA/slotB — see system/rauc/system.conf
# and rpi-tryboot-backend.sh for the A/B scheme this feeds) -----------------
echo "==> Extracting rootA + boot files into a standalone bundle for RAUC"
BUNDLE_DIR="${WORK}/bundle"
mkdir -p "$BUNDLE_DIR"
FINAL_LOOP="$(sudo losetup --show --find --partscan --read-only "$FINAL_IMG")"
udevadm settle 2>/dev/null || true
sudo dd if="${FINAL_LOOP}p2" of="${BUNDLE_DIR}/rootfs.img" bs=4M status=none

BOOT_MNT="${WORK}/boot-mnt"
mkdir -p "$BOOT_MNT"
sudo mount -o ro "${FINAL_LOOP}p1" "$BOOT_MNT"
BOOTFILES_DIR="${BUNDLE_DIR}/bootfiles"
mkdir -p "$BOOTFILES_DIR"
# slotA/slotB/tryboot.txt (repartition.sh's per-device runtime state) never
# ship in the bundle — only the top-level boot files (kernel, initramfs,
# config.txt, cmdline.txt, overlays/) do; the device-side hook (below)
# decides which slot directory they land in at install time.
sudo rsync -rt --exclude /slotA --exclude /slotB --exclude /tryboot.txt \
	"${BOOT_MNT}/" "${BOOTFILES_DIR}/"
sudo umount "$BOOT_MNT"
sudo chown -R "$(id -u):$(id -g)" "$BOOTFILES_DIR"

# cmdline.txt's root= is device- and slot-specific (which of rootA/rootB
# this bundle lands in on a given device isn't known until install time) —
# templated here, filled in by the bundle hook's slot-install step below.
sed -i -E 's/root=LABEL=root[AB]/root=LABEL=__ROOTLABEL__/' "${BOOTFILES_DIR}/cmdline.txt"

tar -C "$BOOTFILES_DIR" -czf "${BUNDLE_DIR}/bootfiles.tar.gz" .

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

# Bundle hook: RAUC has no built-in notion of "a directory inside an
# already-mounted FAT32 partition" as a slot, so the "kernel" custom slot's
# actual install logic ships inside the bundle itself (covered by the same
# signature as every image in it) rather than living as a static
# device-side script. HARDWARE-UNVERIFIED: the argv/env var contract below
# (slot-install, RAUC_SLOT_DEVICE, RAUC_IMAGE_NAME) is reconstructed from
# RAUC's general custom-slot hook conventions — verify against the
# installed rauc version's own docs before trusting this on real hardware.
cat > "${BUNDLE_DIR}/hook.sh" <<'HOOKEOF'
#!/bin/bash
set -euo pipefail
case "${1:-}" in
slot-install)
	TARGET="/boot/firmware/${RAUC_SLOT_DEVICE:?}"
	ROOTLABEL="${RAUC_SLOT_DEVICE#slot}" # slotA -> A, slotB -> B
	mkdir -p "$TARGET"
	find "$TARGET" -mindepth 1 -delete
	tar -xzf "${RAUC_IMAGE_NAME:?}" -C "$TARGET"
	sed -i "s/__ROOTLABEL__/root${ROOTLABEL}/" "${TARGET}/cmdline.txt"
	;;
esac
HOOKEOF
chmod +x "${BUNDLE_DIR}/hook.sh"

cat > "${BUNDLE_DIR}/manifest.raucm" <<EOF
[update]
compatible=slideannouncer-rpi4
version=${IMAGE_VERSION}

[bundle]
format=plain

[hooks]
filename=hook.sh

[image.rootfs]
filename=rootfs.img

[image.kernel]
filename=bootfiles.tar.gz
hooks=install
EOF

BUNDLE_OUT="${DEPLOY_DIR}/${IMG_NAME}-${BUILD_DATE}-${GIT_HASH}.raucb"
echo "==> Building and signing RAUC bundle"
rauc bundle --cert="$RAUC_CERT_PATH" --key="$RAUC_KEY_PATH" "$BUNDLE_DIR" "$BUNDLE_OUT"

echo "==> Done: ${BUNDLE_OUT}"

if [ -n "${USER_PASS:-}" ]; then
	echo "==> Local account 'slideadmin' password (console/keyboard login only): ${USER_PASS}"
else
	# RESUME_WORK skipped the pi-gen stage, so no password was generated
	# this run — the account was already provisioned in the earlier build
	# raw.img came from, and that password was only ever printed there.
	echo "==> RESUME_WORK build: 'slideadmin' password unchanged from the original build (see its output)"
fi
