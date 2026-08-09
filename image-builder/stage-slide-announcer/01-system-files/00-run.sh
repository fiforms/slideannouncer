#!/bin/bash -e
# Installs system/, provisioning/, and local-app/ (staged into ./files/ by
# image-builder/build.sh before the pi-gen build starts) into the rootfs,
# creates the dedicated service user, and enables the units.

install -d "${ROOTFS_DIR}/opt/slide-announcer"
cp -r files/provisioning "${ROOTFS_DIR}/opt/slide-announcer/provisioning"

# local-app itself is never installed onto rootfs — only its built release
# tarball, read-only at a fixed path. system/scripts/local-app-seed.py (run
# every boot, before the backend/kiosk services) extracts this onto /data
# and maintains /data/local-app/current, seeded on first boot and never
# downgraded across an OS update — see local-app/README.md, "Installation
# on the device."
install -d "${ROOTFS_DIR}/opt/slide-announcer/local-app-release"
cp files/local-app-release/local-app.tar.gz "${ROOTFS_DIR}/opt/slide-announcer/local-app-release/local-app.tar.gz"
cp files/local-app-release/VERSION "${ROOTFS_DIR}/opt/slide-announcer/local-app-release/VERSION"
cp files/local-app-release/requirements.txt "${ROOTFS_DIR}/opt/slide-announcer/local-app-release/requirements.txt"

# Version stamp: <kernel-version>-<build-date>-<git-hash>. BUILD_DATE/
# GIT_HASH come from build.sh (host-side, so they reflect the
# slideannouncer repo's commit, not pi-gen's); the kernel version has to be
# read from inside the rootfs itself — `uname -r` in this script would only
# report the x86 build host's kernel, not the image's.
# shellcheck disable=SC1091
. files/BUILD_INFO
KERNEL_VERSION="$(ls "${ROOTFS_DIR}/lib/modules" 2>/dev/null | sort -V | tail -1)"
KERNEL_VERSION="${KERNEL_VERSION:-unknown-kernel}"
echo "${KERNEL_VERSION}-${BUILD_DATE}-${GIT_HASH}" > "${ROOTFS_DIR}/opt/slide-announcer/VERSION"

install -m 644 files/system/*.service "${ROOTFS_DIR}/etc/systemd/system/"
install -d "${ROOTFS_DIR}/usr/local/sbin" "${ROOTFS_DIR}/usr/local/bin"
install -m 755 files/system/scripts/data-resize.sh "${ROOTFS_DIR}/usr/local/sbin/slide-announcer-data-resize.sh"
install -m 755 files/system/scripts/kiosk-start.sh "${ROOTFS_DIR}/usr/local/bin/slide-announcer-kiosk-start.sh"
install -m 755 files/system/scripts/rauc-update.py "${ROOTFS_DIR}/usr/local/sbin/slide-announcer-update"
install -m 755 files/system/scripts/local-app-seed.py "${ROOTFS_DIR}/usr/local/sbin/slide-announcer-local-app-seed"
install -m 755 files/system/scripts/update-check.py "${ROOTFS_DIR}/usr/local/sbin/slide-announcer-update-check"
install -m 755 files/system/scripts/factory-reset-check.sh "${ROOTFS_DIR}/usr/local/sbin/slide-announcer-factory-reset-check"

install -d "${ROOTFS_DIR}/etc/nginx/sites-available"
install -m 644 files/system/nginx-slide-announcer.conf "${ROOTFS_DIR}/etc/nginx/sites-available/slide-announcer.conf"
rm -f "${ROOTFS_DIR}/etc/nginx/sites-enabled/default"
ln -sf ../sites-available/slide-announcer.conf "${ROOTFS_DIR}/etc/nginx/sites-enabled/slide-announcer.conf"

install -d "${ROOTFS_DIR}/etc/polkit-1/rules.d"
install -m 644 files/system/polkit/*.rules "${ROOTFS_DIR}/etc/polkit-1/rules.d/"

# Chromium enterprise policy (not command-line flags — Chromium removed the
# old --disable-save-password-bubble-style switches years ago; policy is
# the only mechanism that still actually works) turning off the password
# manager and autofill, so entering the WiFi password in Settings > Network
# doesn't trigger a confusing "Save password?" bubble on a kiosk with no
# concept of a user account to save it for. Confirmed on real hardware —
# chrome://policy shows this loaded (Debian/Raspberry Pi OS's `chromium`
# package reads /etc/chromium/policies/managed/, not Google Chrome's
# /etc/opt/chrome/... path).
install -d "${ROOTFS_DIR}/etc/chromium/policies/managed"
install -m 644 files/system/chromium-policies/slide-announcer.json \
	"${ROOTFS_DIR}/etc/chromium/policies/managed/"

# RAUC: slot config + stub tryboot backend (see system/rauc/system.conf for
# what's real vs. stubbed) + the public bundle-signing cert build.sh stages
# into files/rauc-keyring.pem — never the private key, which stays on the
# build host/CI secret store and is only ever passed to `rauc bundle`.
install -d "${ROOTFS_DIR}/etc/rauc" "${ROOTFS_DIR}/usr/lib/rauc"
install -m 644 files/system/rauc/system.conf "${ROOTFS_DIR}/etc/rauc/system.conf"
install -m 644 files/rauc-keyring.pem "${ROOTFS_DIR}/etc/rauc/keyring.pem"
install -m 755 files/system/rauc/rpi-tryboot-backend.sh \
	"${ROOTFS_DIR}/usr/lib/rauc/rpi-tryboot-backend.sh"
install -m 755 files/system/rauc/rpi-tryboot-commit.sh \
	"${ROOTFS_DIR}/usr/lib/rauc/rpi-tryboot-commit.sh"
install -d "${ROOTFS_DIR}/etc/tmpfiles.d"
install -m 644 files/system/rauc/slide-announcer-rauc.conf \
	"${ROOTFS_DIR}/etc/tmpfiles.d/slide-announcer-rauc.conf"

# The AnnouncementSlides server this fleet talks to (build.sh validates
# SLIDE_ANNOUNCER_SERVER_URL is set before staging this) — one server per
# fleet, baked in at build time. Read by the local-app backend (pairing/
# sync/heartbeat) and the future RAUC update-check unit alike, so there's
# exactly one place this ever needs to be set.
install -d "${ROOTFS_DIR}/etc/slide-announcer"
install -m 644 files/SERVER_URL "${ROOTFS_DIR}/etc/slide-announcer/server-url"

# Placeholder fstab entry — image-builder/repartition.sh rewrites DATADEV to
# the real PARTUUID of partition 4 once the final partition table exists
# (pi-gen itself only ever produces boot+root, see repartition.sh). The
# mountpoint itself has to exist in the rootfs content too — root is ro (see
# below), so systemd can't create it on demand at boot the way it would on a
# writable root.
install -d "${ROOTFS_DIR}/data"
echo "DATADEV  /data  ext4  defaults,noatime,nofail  0  2" >> "${ROOTFS_DIR}/etc/fstab"

# Read-only rootfs (see SLIDE_ANNOUNCER.md, Tier 1, "Read-only rootfs"): the
# root filesystem itself is mounted ro (below), so anything that needs to
# write to /etc or /var at runtime goes through a CoW overlay instead.
# - /tmp, /var/tmp: plain volatile tmpfs, nothing here needs to survive a
#   reboot.
# - /etc: upper layer lives on /data, not tmpfs — SSH host keys and
#   machine-id (regenerated once by provisioning/firstboot.py) and any
#   future NetworkManager connection profiles under
#   /etc/NetworkManager/system-connections/ must survive reboots, and this
#   way they just do, with no code on top of plain file writes. Caveat this
#   trades for: a RAUC OTA that changes a stock /etc file already shadowed
#   by something in the upper layer won't show through until that shadow is
#   cleared — acceptable here since nothing currently expects OTA to
#   silently rewrite live /etc config out from under a running device.
#   image-builder/repartition.sh pre-creates the upper/work directories on
#   the data partition itself, since they must exist before the very first
#   boot's overlay mount runs.
# - /var: upper layer is tmpfs (/run/overlay-var, created fresh every boot
#   by slide-announcer-overlay-var-dirs.service) — logs, nginx/
#   NetworkManager runtime state, caches. None of it needs to survive a
#   reboot; anything that does (identity, pairing state, slide cache)
#   already lives on /data by design (see "Persistent state discipline").
# x-systemd.requires-mounts-for orders each overlay after the filesystem
# its upperdir/workdir live on; x-systemd.after=slide-announcer-overlay-var-dirs.service
# on the /var line orders it after that unit creates /run/overlay-var's
# upper/work dirs. Deliberately not systemd-tmpfiles-setup.service — that
# unit is ordered after local-fs.target, which can't be reached until
# var.mount succeeds, an ordering cycle systemd resolves by running
# var.mount first, before the dirs exist.
# slide-announcer-overlay-var-dirs.service has DefaultDependencies=no
# specifically to sidestep that cycle.
# The /etc overlay is `nofail`, matching /data's own nofail above — if
# /data doesn't mount, the device still boots with a read-only /etc rather
# than dropping to an emergency shell; the /var overlay never needs nofail
# since its tmpfs backing is always available.
cat >> "${ROOTFS_DIR}/etc/fstab" <<'EOF'
tmpfs   /tmp        tmpfs    nosuid,nodev,mode=1777                                                                      0  0
tmpfs   /var/tmp    tmpfs    nosuid,nodev,mode=1777                                                                      0  0
overlay /etc        overlay  lowerdir=/etc,upperdir=/data/overlay/etc/upper,workdir=/data/overlay/etc/work,x-systemd.requires-mounts-for=/data,nofail    0  0
overlay /var        overlay  lowerdir=/var,upperdir=/run/overlay-var/upper,workdir=/run/overlay-var/work,x-systemd.requires-mounts-for=/run/overlay-var,x-systemd.after=slide-announcer-overlay-var-dirs.service    0  0
EOF
# ROOTDEV is still the literal placeholder text at this point in the build
# — pi-gen's own export-image/04-set-partuuid step substitutes the real
# PARTUUID afterward, once the image is exported.
sed -i 's#\(ROOTDEV\s*/\s*ext4\s*\)defaults,noatime#\1ro,noatime#' "${ROOTFS_DIR}/etc/fstab"

install -d "${ROOTFS_DIR}/etc/systemd/journald.conf.d"
install -m 644 files/system/read-only-root/journald-volatile.conf \
	"${ROOTFS_DIR}/etc/systemd/journald.conf.d/slide-announcer-volatile.conf"

# Quiet boot: move kernel/systemd console messages off tty1 (our kiosk's
# display) onto tty3 instead (still there via Ctrl+Alt+F3 for debugging,
# just not visible on the physical display), and suppress most kernel log
# lines and the blinking cursor. cmdline.txt is a single line — sed edits
# in place rather than risking reordering root=/rootfstype=/etc.
CMDLINE="${ROOTFS_DIR}/boot/firmware/cmdline.txt"
sed -i 's/console=tty1/console=tty3/' "$CMDLINE"
# `ro`: belt-and-suspenders alongside /etc/fstab's ro root entry above — the
# kernel mounts root directly from this cmdline (no initramfs in this
# image), before /etc/fstab is even read, so this is what actually makes
# the very first mount read-only; the fstab entry only matters afterward if
# systemd-remount-fs.service re-evaluates the options.
sed -i 's/$/ ro quiet loglevel=3 logo.nologo vt.global_cursor_default=0/' "$CMDLINE"
# ...and disable the early rainbow test-pattern splash (shown by the GPU
# firmware before Linux even loads) for a solid black screen instead.
printf '\n[all]\ndisable_splash=1\n' >> "${ROOTFS_DIR}/boot/firmware/config.txt"

# Fleet-specific hardware config.txt lines (fan control overlays, etc. —
# see SLIDE_ANNOUNCER_BOOT_CONFIG_EXTRA in .env.example), under their own
# unconditional [all] section same as disable_splash above. This survives
# RAUC OTA/tryboot switching for free rather than needing special-casing:
# image-builder/repartition.sh mirrors this same partition-root config.txt
# into boot/firmware/slotA/ for the initial image, and every future OTA
# bundle is built from this same 00-run.sh too, so slotB/config.txt (and
# the file rpi-tryboot-commit.sh copies back to the partition root on
# commit) always carries whatever's set here at build time.
if [ -s files/BOOT_CONFIG_EXTRA ]; then
	printf '\n[all]\n' >> "${ROOTFS_DIR}/boot/firmware/config.txt"
	cat files/BOOT_CONFIG_EXTRA >> "${ROOTFS_DIR}/boot/firmware/config.txt"
	printf '\n' >> "${ROOTFS_DIR}/boot/firmware/config.txt"
fi

# Root password — debugging/development only (see .env.example and
# build.sh, which only stages this file when ROOT_DEV_PASSWORD is set).
# Exported (not just a shell variable) so it's visible inside on_chroot's
# capsh-spawned bash below; the heredoc itself is single-quoted, so this
# only ever gets read at chroot runtime, never substituted into the script
# text on the host.
if [ -f files/ROOT_DEV_PASSWORD ]; then
	export ROOT_DEV_PASSWORD="$(cat files/ROOT_DEV_PASSWORD)"
fi
export WIFI_COUNTRY="$(cat files/WIFI_COUNTRY)"

on_chroot << 'EOF'
if [ -n "${ROOT_DEV_PASSWORD:-}" ]; then
	echo "root:${ROOT_DEV_PASSWORD}" | chpasswd
fi

# The WiFi radio ships soft rfkill-blocked until a regulatory domain is
# set — a kernel/cfg80211 requirement, not something NetworkManager/nmcli
# can work around from the device side (see SLIDE_ANNOUNCER_WIFI_COUNTRY in
# image-builder/.env.example). raspi-config's own do_wifi_country is used
# rather than hand-writing config files, since it's the one mechanism the
# Raspberry Pi Foundation keeps correct across OS releases regardless of
# which network stack is active. `|| true`: this chroot has no real WiFi
# radio (qemu-user, build host's kernel underneath), so the live
# rfkill-unblock/`iw reg set` side effects raspi-config also attempts can
# harmlessly fail here — what actually matters is the persisted config it
# writes, applied for real on the device's first real boot.
# Confirmed on real hardware — `rfkill list` shows wifi unblocked on first
# boot without a manual raspi-config run.
raspi-config nonint do_wifi_country "${WIFI_COUNTRY}" || true

# Belt-and-suspenders alongside build.sh setting WPA_COUNTRY for pi-gen's
# own stage2/02-net-tweaks/01-run.sh (which otherwise bakes
# WirelessEnabled=false here when WPA_COUNTRY is unset — a NetworkManager-
# level radio-off flag, separate from the kernel rfkill block above):
# force it back to true unconditionally, regardless of what that earlier
# stage decided. /var is a tmpfs overlay reset every boot (see
# read-only-root, above), so whatever's baked into this real file is what
# every single boot actually gets — a live `nmcli radio wifi on` on a
# running device never survives a reboot without this being right here.
mkdir -p /var/lib/NetworkManager
cat > /var/lib/NetworkManager/NetworkManager.state << 'NMEOF'
[main]
WirelessEnabled=true
NMEOF

useradd --system --create-home --home-dir /var/lib/slide-announcer \
	--groups video,render,input,dialout,netdev slideannouncer

chown -R slideannouncer:slideannouncer /opt/slide-announcer

# Fixed OS-image infra, independent of which app release is current on
# /data (see local-app/README.md, "Installation on the device") — a future
# app-only update via the updater is expected to be code-only, not a new
# dependency; a requirements.txt change ships alongside an OS update instead.
python3 -m venv /opt/slide-announcer/venv
/opt/slide-announcer/venv/bin/pip install --no-cache-dir \
	-r /opt/slide-announcer/local-app-release/requirements.txt
chown -R slideannouncer:slideannouncer /opt/slide-announcer/venv

# The stock root-resize first-boot unit conflicts with our fixed-size rootA
# design (see image-builder/repartition.sh) — only /data auto-expands here.
systemctl disable rpi-resizerootfs.service 2>/dev/null || true
systemctl mask rpi-resizerootfs.service 2>/dev/null || true

# Our kiosk owns tty1 exclusively. `mask` (not `disable`) — getty@tty1 is
# enabled via systemd's vendor preset rather than an explicit symlink, and
# `disable` has nothing concrete to remove for that in an offline chroot
# with no systemd/dbus to evaluate presets against, so it silently no-ops.
# `mask` unconditionally symlinks to /dev/null regardless of preset state.
systemctl mask getty@tty1.service

systemctl enable slide-announcer-overlay-var-dirs.service
systemctl enable slide-announcer-factory-reset-check.service
systemctl enable slide-announcer-data-dirs.service
systemctl enable slide-announcer-data-resize.service
systemctl enable slide-announcer-firstboot.service
systemctl enable slide-announcer-local-app-seed.service
systemctl enable slide-announcer-backend.service
systemctl enable slide-announcer-kiosk.service
systemctl enable slide-announcer-tryboot-check.service
systemctl enable nginx.service
systemctl enable seatd.service
EOF
