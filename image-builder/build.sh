#!/bin/bash
# Top-level entrypoint: builds the Slide Announcer Raspberry Pi image.
#
#   ./build.sh                 normal build (no SSH; a random per-build
#                               console-login password is still printed —
#                               see image-builder/README.md). SSH access
#                               (see SLIDE_ANNOUNCER_ENABLE_SSH/
#                               SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH in
#                               .env.example) is always key-based only —
#                               there is no password-over-SSH option, dev
#                               or otherwise; password auth is disabled
#                               globally in every image, unconditionally.
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

# The AnnouncementSlides server this device talks to (pairing, sync,
# heartbeat, OTA update checks) now lives in slideannouncer.yaml on the
# boot partition (see that file's own comment), not baked into the image
# at build time — one image can serve multiple independent fleets/servers
# just by swapping that file per device. Setting this here is optional:
# if set, it's only used to seed a default `server_url` into
# slideannouncer.yaml.example (same as SSH_ENABLED below), so a build
# still produces a device that works out of the box without a post-flash
# edit. Leave it unset to ship a fully generic image with no default.
SLIDE_ANNOUNCER_SERVER_URL="${SLIDE_ANNOUNCER_SERVER_URL:-}"
if [ -n "$SLIDE_ANNOUNCER_SERVER_URL" ]; then
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
fi

# WiFi regulatory domain (ISO 3166-1 alpha-2, e.g. US) — the Pi's WiFi
# radio ships soft rfkill-blocked until this is set (a kernel/cfg80211
# requirement, not something nmcli/NetworkManager can work around from the
# device side), so without this every device would need someone at the
# console running raspi-config by hand before Settings > Network's WiFi
# scan could ever see anything. Seeded into the staged network-config's
# regulatory-domain below (applied by cloud-init/netplan at first boot), not
# baked in via raspi-config at build time. Optional, defaults to US — this
# fleet's deployment target — since getting a device on WiFi at all matters
# more here than failing the build over a missing regulatory code.
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

# This is what a freshly built image ships with by default — sshd itself
# is `systemctl enable`d unconditionally now (see ENABLE_SSH=1 in
# ./config), password authentication over SSH is disabled globally and
# unconditionally in every image regardless of these vars (see
# system/ssh/pubkey-only.conf, always installed — there is no build mode
# that ever ships password-over-SSH), and actually starting sshd at all is
# a runtime, boot-yaml decision (see system/ssh/ssh-gate.conf), not a
# build-time one. Both of SLIDE_ANNOUNCER_ENABLE_SSH/
# SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH must still be set together for the
# shipped default to be "on" with this key, though — a bare "enable SSH"
# flag with no key would leave sshd allowed to start with no key anyone
# could actually authenticate with, and a bare key with no flag would be
# silently inert, so neither alone does anything.
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

# OS_VERSION is this project's own semver (image-builder/VERSION), bumped
# manually per release — NOT derived from the kernel/build-date/git-hash
# below, which are kept only as build provenance (visible in the build
# log, no longer part of any filename or the on-device VERSION stamp) so
# OTA bundles/hotfixes can name themselves after, and gate on, a clean,
# human-meaningful version instead of a build fingerprint.
OS_VERSION="$(cat "${HERE}/VERSION")"

# Build provenance (log/debugging only — see OS_VERSION above for what
# actually names files and gets written to /opt/slide-announcer/VERSION).
# Computed unconditionally (even on RESUME_WORK) since 00-run.sh expects
# BUILD_INFO to exist either way.
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
# Seed the staged network-config's WiFi regulatory-domain from
# SLIDE_ANNOUNCER_WIFI_COUNTRY (validated above) — see that file's own
# comment for why this block is live rather than commented out. Netplan
# applies "regulatory-domain" itself (an "iw reg set" at cloud-init's first
# boot), independent of NetworkManager handling the actual WiFi connection,
# so this doesn't conflict with the rest of that file's NetworkManager-era
# examples.
{
	echo ""
	echo "network:"
	echo "  version: 2"
	echo "  regulatory-domain: ${SLIDE_ANNOUNCER_WIFI_COUNTRY}"
} >> "${FILES_DIR}/system/cloud-init/network-config"
# SSH is now always `systemctl enable`d at the image level
# (image-builder/config's ENABLE_SSH=1) and password authentication is
# always disabled globally (system/ssh/pubkey-only.conf, unconditionally
# installed below by 00-run.sh — there is no build mode that ever ships
# password-over-SSH). Whether sshd actually starts at all is gated at
# runtime by system/ssh/ssh-gate.conf's ExecStartPre checking
# slideannouncer.yaml's `ssh_enabled: true` (see system/scripts/ssh-gate.py).
# Seed both that field and the key itself into the staged yaml.example
# when SLIDE_ANNOUNCER_ENABLE_SSH/SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH are
# set (both validated above), so a device built with a key configured
# doesn't need a boot-yaml edit just to make SSH match what was asked for
# at build time. Leaving both unset (the plain `./build.sh` case) means
# SSH stays unreachable until someone deliberately opts in later by
# editing the yaml (adding both ssh_enabled: true and a key of their own)
# and rebooting — the same fail-closed default the old build-time
# ENABLE_SSH=0 gave.
#
# Appended into the staged slideannouncer.yaml.example's
# ssh_authorized_keys field rather than baked straight into
# /home/slideadmin/.ssh/authorized_keys at image-build time: that path is
# now bind-mounted onto /data (see system/slide-announcer-home-dirs.service
# + 00-run.sh's fstab entry) — anything written to it at build time would
# just be invisible the moment that bind mount takes effect on first boot,
# then permanently unreachable since /data survives every future OTA and
# this image doesn't. Going through the yaml instead means
# provisioning/firstboot.py's sync_ssh_authorized_keys() writes the real
# file at runtime, every boot, with the same content persisted on /data —
# see that function's own docstring for why this also makes key
# rotation/revocation an edit-and-reboot instead of a rebuild-and-reflash.
if [ "$SSH_ENABLED" = "1" ]; then
	{
		echo "ssh_enabled: true"
		echo ""
		echo "ssh_authorized_keys: |"
		sed 's/^/  /' "$SLIDE_ANNOUNCER_SSH_PUBLIC_KEY_PATH"
	} >> "${FILES_DIR}/provisioning/slideannouncer.yaml.example"
fi
# Same rationale as the ssh_authorized_keys append above: seeded into the
# yaml (read at runtime, per SLIDE_ANNOUNCER.md) rather than a separate
# build-time file, so this is just a convenience default — not required,
# and swappable per-device after the fact with no rebuild/reflash.
if [ -n "$SLIDE_ANNOUNCER_SERVER_URL" ]; then
	echo "server_url: ${SLIDE_ANNOUNCER_SERVER_URL}" >> "${FILES_DIR}/provisioning/slideannouncer.yaml.example"
fi
# Fixed OS-image infra, like local-app-seed.py — deliberately NOT part of
# the versioned local-app release tarball below, so a bad app update can
# never take the update mechanism itself down with it.
rsync -a "${REPO_ROOT}/updater/" "${FILES_DIR}/updater/" --exclude 'README.md'
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
	echo "OS_VERSION=${OS_VERSION}"
	echo "BUILD_DATE=${BUILD_DATE}"
	echo "GIT_HASH=${GIT_HASH}"
} > "${FILES_DIR}/BUILD_INFO"
echo "==> Building slideannouncer ${OS_VERSION} (provenance: date=${BUILD_DATE} git=${GIT_HASH})"

# Only a public cert goes into the image's RAUC keyring (installed by
# 00-run.sh) — RAUC_KEYRING_CERT_PATH, not RAUC_CERT_PATH (the two are the
# same file in dev mode, but the CA cert vs. the signing cert in
# production mode — see .env.example). No private key ever gets staged
# here; it's only ever passed as an argument to `rauc bundle` below.
cp "$RAUC_KEYRING_CERT_PATH" "${FILES_DIR}/rauc-keyring.pem"

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
	# WPA_COUNTRY deliberately left unset: the WiFi regulatory domain is now
	# set via network-config's regulatory-domain (seeded above from
	# SLIDE_ANNOUNCER_WIFI_COUNTRY), not pi-gen's own stage2/02-net-tweaks/
	# 01-run.sh. With WPA_COUNTRY unset, that stock stage instead bakes
	# WirelessEnabled=false into /var/lib/NetworkManager/NetworkManager.state
	# — a NetworkManager-level radio-off flag, separate from the kernel
	# rfkill block — which stage-slide-announcer/01-system-files/00-run.sh
	# unconditionally overrides back to true afterward (see that script's
	# own comment; /var is a tmpfs overlay reset every boot, so whatever's
	# baked into that real file is what every boot actually gets).
} >> "$CONFIG_FILE"
echo "==> Local account 'slideadmin' password (console/keyboard login only): ${USER_PASS}"

# ENABLE_SSH itself is no longer conditional here — image-builder/config
# sets it to 1 unconditionally now, and system/ssh/ssh-gate.conf's
# ExecStartPre gates actual sshd startup on slideannouncer.yaml's
# `ssh_enabled: true` at runtime instead (see that file and
# system/scripts/ssh-gate.py). Deliberately NOT pi-gen's own
# PUBKEY_ONLY_SSH/PUBKEY_SSH_FIRST_USER knobs for the key-based path
# either: those write the key straight into
# /home/slideadmin/.ssh/authorized_keys at build time, which is now bind-
# mounted onto /data on first boot (see
# system/slide-announcer-home-dirs.service) — anything baked there would
# just be shadowed and unreachable from the moment that mount takes
# effect, and pi-gen's own build.sh hard-requires PUBKEY_SSH_FIRST_USER to
# be set whenever PUBKEY_ONLY_SSH=1, so there'd be no way to opt into
# pubkey-only mode there without also feeding it a key it can never
# actually use. system/ssh/pubkey-only.conf (unconditionally installed by
# 00-run.sh, in every image) covers the "disable password auth" half
# instead. (The "SSH enabled for slideadmin" log line for this path is
# already printed above, at validation time.)

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
# pi-gen/deploy accumulates one dated image per run (never cleaned between
# builds) and its filename carries the build DATE, not this project's
# OS_VERSION — so multiple old images can match this glob. `find | head -n1`
# picked whatever the filesystem happened to list first, which silently
# grabbed a stale prior build instead of the one just produced. Sort by
# mtime and take the newest.
RAW_XZ="$(find "${PI_GEN_DIR}/deploy" -maxdepth 1 -name "*${IMG_NAME}*.img.xz" ! -name '*-lite*' -printf '%T@ %p\n' | sort -rn | head -n1 | cut -d' ' -f2-)"
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

OUT_NAME="${IMG_NAME}-${OS_VERSION}.img.xz"
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

# rootA's partition-table entry still declares the full fixed size (5GiB by
# default — reserved for future, larger RAUC bundles), but repartition.sh
# truncated FINAL_IMG right after rootA's actual (shrunk) filesystem, so the
# file has no bytes for the rest of that declared range. Reading rootA via
# a loop partition device (sized off the partition table), or re-deriving
# its range with parted/fdisk against the now-truncated file, both run into
# the same "partition outside the disk" problem — read the exact byte range
# repartition.sh already worked out, straight out of FINAL_IMG.
read -r ROOTA_START ROOTA_FS_BYTES < "${FINAL_IMG}.rootA-range"
dd if="$FINAL_IMG" of="${BUNDLE_DIR}/rootfs.img" bs=1M \
	skip="$ROOTA_START" count="$ROOTA_FS_BYTES" iflag=skip_bytes,count_bytes status=none

FINAL_LOOP="$(sudo losetup --show --find --partscan --read-only "$FINAL_IMG")"
udevadm settle 2>/dev/null || true

BOOT_MNT="${WORK}/boot-mnt"
mkdir -p "$BOOT_MNT"
sudo mount -o ro "${FINAL_LOOP}p1" "$BOOT_MNT"
BOOTFILES_DIR="${BUNDLE_DIR}/bootfiles"
mkdir -p "$BOOTFILES_DIR"
# Captured from slotA/ specifically, NOT the boot partition's top level —
# repartition.sh's layout keeps kernel/initramfs/.dtbs/overlays/
# cmdline.txt only in slotA/slotB (RAUC's per-slot "kernel" class), never
# duplicated at the top level, which holds only config.txt and the
# VideoCore firmware blobs (start*.elf/fixup*.dat/bootcode.bin) that
# os_prefix doesn't cover at all — see repartition.sh's own comment for
# why (confirmed by testing: moving those breaks boot outright). The
# build host's own active slot is always slotA, per that same design.
sudo rsync -rt "${BOOT_MNT}/slotA/" "${BOOTFILES_DIR}/"
sudo umount "$BOOT_MNT"
sudo chown -R "$(id -u):$(id -g)" "$BOOTFILES_DIR"

# cmdline.txt's root= is device- and slot-specific (which of rootA/rootB
# this bundle lands in on a given device isn't known until install time,
# and the ACTUAL PARTUUID of that slot on that specific device isn't
# known at build time either) — templated here as a placeholder, resolved
# dynamically on-device by the bundle hook's slot-install step below via
# blkid, after the rootfs slot's own install has already relabeled the
# target partition. PARTUUID, not LABEL: confirmed by testing on real
# hardware that root=LABEL=... does not reliably resolve during a tryboot
# (os_prefix) boot — it panics ("Unable to mount root fs on
# unknown-block(0,0)") even with a generous rootdelay=, while the
# identical partition referenced by root=PARTUUID= boots clean every
# time. Also strip any rauc.slot=rootfs.N already present: this file is
# rsynced off the just-built image's OWN boot partition, which
# repartition.sh already stamped with rauc.slot=rootfs.0 (since the build
# machine's own rootA always says rootfs.0) — left in place, the hook
# below would just append a second, conflicting rauc.slot= next to it
# instead of replacing it, and a cmdline with two rauc.slot= arguments
# produced exactly the same panic. That marker is inherently specific to
# whichever slot a bundle actually installs into on a given device, never
# something to inherit from the build machine's own current state.
sed -i -E 's/root=PARTUUID=[0-9a-fA-F-]+/root=PARTUUID=__ROOTPARTUUID__/; s/ rauc\.slot=rootfs\.[01]//' \
	"${BOOTFILES_DIR}/cmdline.txt"

tar -C "$BOOTFILES_DIR" -czf "${BUNDLE_DIR}/bootfiles.tar.gz" .

sudo losetup -d "$FINAL_LOOP"
sudo chown "$(id -u):$(id -g)" "${BUNDLE_DIR}/rootfs.img"

# Read the version stamp 00-run.sh wrote into the image itself (same
# OS_VERSION value, just verified round-trip) rather than reusing the
# host-side variable directly, so the RAUC manifest's version always
# matches what a running device actually reports via
# /opt/slide-announcer/VERSION — catches a packaging bug rather than
# assuming it can't happen.
ROOTFS_MNT="${WORK}/rootfs-mnt"
mkdir -p "$ROOTFS_MNT"
sudo mount -o ro,loop "${BUNDLE_DIR}/rootfs.img" "$ROOTFS_MNT"
IMAGE_VERSION="$(cat "${ROOTFS_MNT}/opt/slide-announcer/VERSION")"
sudo umount "$ROOTFS_MNT"

# If these don't match, the rootfs baked into the image is stale relative to
# what this build run intended (e.g. a resumed pi-gen container skipped the
# stage that stamps VERSION) — fail loudly instead of silently shipping a
# mislabeled bundle.
if [ "$IMAGE_VERSION" != "$OS_VERSION" ]; then
	echo "ERROR: version mismatch — built rootfs reports VERSION=${IMAGE_VERSION} but this build run is OS_VERSION=${OS_VERSION}." >&2
	echo "This usually means pi-gen reused a stale/resumed work container and skipped stamping the version. Remove the pigen_work container (docker rm -v pigen_work) and rebuild from clean." >&2
	exit 1
fi

# Bundle hook: RAUC has no built-in notion of "a directory inside an
# already-mounted FAT32 partition" as a slot, so the "kernel" custom slot's
# actual install logic ships inside the bundle itself (covered by the same
# signature as every image in it) rather than living as a static
# device-side script. Hook argv values (slot-install, slot-post-install) and
# env vars (RAUC_SLOT_DEVICE, RAUC_SLOT_BOOTNAME, RAUC_IMAGE_NAME) are
# confirmed against RAUC 1.13's own source (src/update_handler.c's
# R_SLOT_HOOK_* defines) — this contract itself was not a guess, and the
# full install/tryboot/commit cycle is now confirmed end-to-end on real
# hardware too (see rpi-tryboot-backend.sh and rpi-tryboot-commit.sh).
#
# slot-post-install on [image.rootfs]: RAUC's default install for an ext4
# slot with a full raw filesystem image is a plain byte copy onto the slot
# device — it does not reformat/relabel/rewrite anything. This bundle's
# rootfs.img is dd'd straight off the currently-active slot (see the dd
# call above), so its ext4 superblock label AND all three of its
# /etc/fstab device lines (root, /boot/firmware, /data) still carry
# whatever the BUILD MACHINE's own values were — not just a stale label,
# but literally a different disk's PARTUUIDs entirely, since every
# device's NEW_DISK_ID is randomly assigned per build/per device (see
# repartition.sh). Confirmed by testing: this left /data mounting against
# a PARTUUID that doesn't exist on the actual device at all, which stalls
# boot indefinitely (systemd-remount-fs/the /etc overlay's
# requires-mounts-for=/data waiting on a device unit that can never
# appear). Fixing all three lines by MOUNTPOINT (not by matching whatever
# stale device spec happens to be there) and re-deriving each PARTUUID
# fresh via blkid on THIS device, after every install, handles this
# regardless of which direction (A->B or B->A) the update runs. e2label
# is kept too, even though nothing below still depends on the ext4 label
# for booting — purely so blkid/lsblk output stays human-readable for
# whoever's debugging next.
#
# The exact same staleness hits /etc/rauc/system.conf's own
# [slot.rootfs.0]/[slot.rootfs.1] device= lines, for the same underlying
# reason (baked in by repartition.sh at THIS RELEASE's build time, not
# this device's), but it went unfixed here for a long time — confirmed
# on real hardware (2026-08-13): a device on its second full-OS OTA had
# fstab/cmdline.txt correctly pointing at its real disk's PARTUUIDs (the
# fix below already covered those) while system.conf still referenced the
# unrelated build machine's disk id, so `rauc install`'s NEXT run failed
# outright ("Destination device ... for slot 'rootfs.0' not found") even
# though the device itself booted and ran fine. Fixed the same way as the
# fstab lines: by LOCATION (each PARTUUID substituted only within its own
# [slot.rootfs.N] stanza, never by matching the stale value, since that
# value is different garbage on every release build) rather than assuming
# anything about what's currently there. blkid -L rootA/rootB (not
# RAUC_SLOT_DEVICE) for both, since by this point in slot-post-install
# both partitions carry valid, correctly-labeled filesystems regardless of
# which one this particular install just wrote — the just-installed one
# from the e2label call two lines up, the other from whenever it was last
# installed (or the original flash, if it never has been).
cat > "${BUNDLE_DIR}/hook.sh" <<'HOOKEOF'
#!/bin/bash
set -euo pipefail
case "${1:-}" in
slot-install)
	TARGET="${RAUC_SLOT_DEVICE:?}"
	ROOTLABEL="${TARGET##*/slot}" # /boot/firmware/slotA -> A, slotB -> B
	# /boot/firmware is ro by default as of this release (see 00-run.sh's
	# fstab entry) — but this hook can also run on a device still booted
	# into an OS build from before that existed, where it's already rw.
	# Inlined rather than calling slide-announcer-bootfw-remount: that
	# helper is only guaranteed to exist on a device already running the
	# same release this hook ships in, not on whatever's live right now.
	#
	# Deliberately does NOT remount back to ro afterward the way every
	# other writer of this partition does: system.conf's
	# bootloader-custom-backend is a FIXED path on whatever's currently
	# running (/usr/lib/rauc/rpi-tryboot-backend.sh), and RAUC calls that
	# script's set-primary right after this hook finishes, still within
	# the same `rauc install` — on a device mid-upgrade FROM a pre-this-
	# release OS, that's the OLD backend script, which doesn't know to
	# remount rw itself and would break on a ro partition. Leaving it rw
	# here and letting the next boot's fstab mount reassert ro is a much
	# smaller exposure window (until that reboot, which a tryboot cycle
	# triggers almost immediately anyway) than risking that ordering.
	mount -o remount,rw /boot/firmware 2>/dev/null || true
	mkdir -p "$TARGET"
	find "$TARGET" -mindepth 1 -delete
	# RAUC_IMAGE_NAME is already an absolute path (e.g.
	# "/mnt/rauc/bundle/bootfiles.tar.gz"), not a bare filename — an
	# earlier pass at this line guessed otherwise from RAUC's own docs
	# ("the file name of the image", read as a bare name) and prefixed it
	# with RAUC_BUNDLE_MOUNT_POINT, which actually broke it: journalctl
	# showed the doubled path this produced verbatim ("tar (child):
	# /mnt/rauc/bundle//mnt/rauc/bundle/bootfiles.tar.gz: Cannot open").
	# Use it as-is. --no-same-owner: TARGET is FAT32 (/boot/firmware/...),
	# which has no concept of Unix ownership at all — tar's default attempt
	# to chown extracted files to their original uid/gid fails outright
	# there (confirmed by testing: "Cannot change ownership to uid 1000,
	# gid 1000: Operation not permitted", fatal under set -e once tar's own
	# exit code reflects it).
	tar --no-same-owner -xzf "${RAUC_IMAGE_NAME:?}" -C "$TARGET"
	# Depends on the rootfs slot's own slot-post-install hook (below)
	# having already relabeled the target partition — confirmed by testing
	# that RAUC always installs [image.rootfs] before [image.kernel] (this
	# manifest's own declaration order), so "root${ROOTLABEL}" already
	# resolves to the just-written partition by the time this runs.
	ROOT_PARTUUID="$(blkid -s PARTUUID -o value "$(blkid -L "root${ROOTLABEL}")")"
	sed -i -E "s/__ROOTPARTUUID__/${ROOT_PARTUUID}/; s#(root=PARTUUID=${ROOT_PARTUUID})#\1 rauc.slot=rootfs.$([ "$ROOTLABEL" = A ] && echo 0 || echo 1)#" \
		"${TARGET}/cmdline.txt"
	;;
slot-post-install)
	e2label "${RAUC_SLOT_DEVICE:?}" "root${RAUC_SLOT_BOOTNAME:?}"
	# [image.rootfs]'s hooks=post-install makes RAUC auto-mount this slot
	# and provide RAUC_SLOT_MOUNT_POINT for exactly this kind of fixup.
	mount -o remount,rw "${RAUC_SLOT_MOUNT_POINT:?}"
	ROOT_PARTUUID="$(blkid -s PARTUUID -o value "${RAUC_SLOT_DEVICE:?}")"
	BOOT_PARTUUID="$(blkid -s PARTUUID -o value "$(blkid -L bootfs)")"
	DATA_PARTUUID="$(blkid -s PARTUUID -o value "$(blkid -L data)")"
	sed -i -E \
		-e "s#^\S+(\s+/\s)#PARTUUID=${ROOT_PARTUUID}\1#" \
		-e "s#^\S+(\s+/boot/firmware\s)#PARTUUID=${BOOT_PARTUUID}\1#" \
		-e "s#^\S+(\s+/data\s)#PARTUUID=${DATA_PARTUUID}\1#" \
		"${RAUC_SLOT_MOUNT_POINT}/etc/fstab"
	# system.conf's own [slot.rootfs.0]/[slot.rootfs.1] device= lines are
	# just as stale as fstab's were (see this hook's header comment) —
	# same fix, by LOCATION (each address range scoped to its own
	# [slot.rootfs.N] stanza) rather than by matching whatever garbage
	# value happens to already be there.
	ROOTA_PARTUUID="$(blkid -s PARTUUID -o value "$(blkid -L rootA)")"
	ROOTB_PARTUUID="$(blkid -s PARTUUID -o value "$(blkid -L rootB)")"
	sed -i -E \
		-e "/^\[slot\.rootfs\.0\]/,/^\[/{s#^device=/dev/disk/by-partuuid/[0-9a-fA-F-]+#device=/dev/disk/by-partuuid/${ROOTA_PARTUUID}#}" \
		-e "/^\[slot\.rootfs\.1\]/,/^\[/{s#^device=/dev/disk/by-partuuid/[0-9a-fA-F-]+#device=/dev/disk/by-partuuid/${ROOTB_PARTUUID}#}" \
		"${RAUC_SLOT_MOUNT_POINT}/etc/rauc/system.conf"
	;;
esac
HOOKEOF
chmod +x "${BUNDLE_DIR}/hook.sh"

cat > "${BUNDLE_DIR}/manifest.raucm" <<EOF
[update]
compatible=slideannouncer-rpi4
version=${IMAGE_VERSION}

# verity, not plain: "rauc install <url>" streams the bundle over HTTP
# rather than downloading it whole first, and RAUC's streaming installer
# only supports formats that can be authenticated block-by-block as they
# arrive (confirmed by testing: plain format failed with "Bundle format
# 'plain' not supported in streaming mode"). With verity, the manifest
# lives in the bundle's CMS signature itself (readable without hashing the
# full squashfs payload first), and a dm-verity hash tree authenticates
# each block on demand — rauc bundle computes the verity-hash/salt/size
# metadata automatically; nothing else in this bundle's construction
# needs to change for this format.
[bundle]
format=verity

[hooks]
filename=hook.sh

[image.rootfs]
filename=rootfs.img
hooks=post-install

[image.kernel]
filename=bootfiles.tar.gz
hooks=install
EOF

BUNDLE_OUT="${DEPLOY_DIR}/${IMG_NAME}-${OS_VERSION}.raucb"
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

# pi-gen/deploy accumulates one dated .img.xz (+ .info) per run and is never
# cleaned by pi-gen itself — left alone, these pile up and eat disk (and is
# exactly what caused build.sh to grab a stale prior build's image above).
# Only safe to do once we've actually consumed this run's output above (i.e.
# not a RESUME_WORK run, which never touches pi-gen/deploy at all).
if [ -z "$RESUME_WORK" ]; then
	echo "==> Cleaning up pi-gen/deploy (consumed raw images no longer needed)"
	find "${PI_GEN_DIR}/deploy" -maxdepth 1 \( -name "*${IMG_NAME}*.img.xz" -o -name "*${IMG_NAME}*.info" \) -delete
fi
