#!/bin/bash -e
# Installs system/, provisioning/, and local-app/ (staged into ./files/ by
# image-builder/build.sh before the pi-gen build starts) into the rootfs,
# creates the dedicated service user, and enables the units.

install -d "${ROOTFS_DIR}/opt/slide-announcer"
cp -r files/provisioning "${ROOTFS_DIR}/opt/slide-announcer/provisioning"
cp -r files/local-app "${ROOTFS_DIR}/opt/slide-announcer/local-app"

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

install -d "${ROOTFS_DIR}/etc/nginx/sites-available"
install -m 644 files/system/nginx-slide-announcer.conf "${ROOTFS_DIR}/etc/nginx/sites-available/slide-announcer.conf"
rm -f "${ROOTFS_DIR}/etc/nginx/sites-enabled/default"
ln -sf ../sites-available/slide-announcer.conf "${ROOTFS_DIR}/etc/nginx/sites-enabled/slide-announcer.conf"

install -d "${ROOTFS_DIR}/etc/polkit-1/rules.d"
install -m 644 files/system/polkit/50-networkmanager-slide-announcer.rules \
	"${ROOTFS_DIR}/etc/polkit-1/rules.d/"

# RAUC: slot config + stub tryboot backend (see system/rauc/system.conf for
# what's real vs. stubbed) + the public bundle-signing cert build.sh stages
# into files/rauc-keyring.pem — never the private key, which stays on the
# build host/CI secret store and is only ever passed to `rauc bundle`.
install -d "${ROOTFS_DIR}/etc/rauc" "${ROOTFS_DIR}/usr/lib/rauc"
install -m 644 files/system/rauc/system.conf "${ROOTFS_DIR}/etc/rauc/system.conf"
install -m 644 files/rauc-keyring.pem "${ROOTFS_DIR}/etc/rauc/keyring.pem"
install -m 755 files/system/rauc/rpi-tryboot-backend.sh \
	"${ROOTFS_DIR}/usr/lib/rauc/rpi-tryboot-backend.sh"
install -d "${ROOTFS_DIR}/etc/tmpfiles.d"
install -m 644 files/system/rauc/slide-announcer-rauc.conf \
	"${ROOTFS_DIR}/etc/tmpfiles.d/slide-announcer-rauc.conf"

# Placeholder fstab entry — image-builder/repartition.sh rewrites DATADEV to
# the real PARTUUID of partition 4 once the final partition table exists
# (pi-gen itself only ever produces boot+root, see repartition.sh).
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
#   by system/read-only-root/overlay-var.conf below) — logs, nginx/
#   NetworkManager runtime state, caches. None of it needs to survive a
#   reboot; anything that does (identity, pairing state, slide cache)
#   already lives on /data by design (see "Persistent state discipline").
# x-systemd.requires-mounts-for orders each overlay after the filesystem
# its upperdir/workdir live on; x-systemd.after=systemd-tmpfiles-setup.service
# on the /var line orders it after overlay-var.conf's directories exist.
# The /etc overlay is `nofail`, matching /data's own nofail above — if
# /data doesn't mount, the device still boots with a read-only /etc rather
# than dropping to an emergency shell; the /var overlay never needs nofail
# since its tmpfs backing is always available.
cat >> "${ROOTFS_DIR}/etc/fstab" <<'EOF'
tmpfs   /tmp        tmpfs    nosuid,nodev,mode=1777                                                                      0  0
tmpfs   /var/tmp    tmpfs    nosuid,nodev,mode=1777                                                                      0  0
overlay /etc        overlay  lowerdir=/etc,upperdir=/data/overlay/etc/upper,workdir=/data/overlay/etc/work,x-systemd.requires-mounts-for=/data,nofail    0  0
overlay /var        overlay  lowerdir=/var,upperdir=/run/overlay-var/upper,workdir=/run/overlay-var/work,x-systemd.requires-mounts-for=/run/overlay-var,x-systemd.after=systemd-tmpfiles-setup.service    0  0
EOF
# ROOTDEV is still the literal placeholder text at this point in the build
# — pi-gen's own export-image/04-set-partuuid step substitutes the real
# PARTUUID afterward, once the image is exported.
sed -i 's#\(ROOTDEV\s*/\s*ext4\s*\)defaults,noatime#\1ro,noatime#' "${ROOTFS_DIR}/etc/fstab"

install -m 644 files/system/read-only-root/overlay-var.conf \
	"${ROOTFS_DIR}/etc/tmpfiles.d/slide-announcer-overlay-var.conf"
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

on_chroot << 'EOF'
useradd --system --create-home --home-dir /var/lib/slide-announcer \
	--groups video,render,input,dialout,netdev slideannouncer

chown -R slideannouncer:slideannouncer /opt/slide-announcer

python3 -m venv /opt/slide-announcer/local-app/backend/venv
/opt/slide-announcer/local-app/backend/venv/bin/pip install --no-cache-dir \
	-r /opt/slide-announcer/local-app/backend/requirements.txt
chown -R slideannouncer:slideannouncer /opt/slide-announcer/local-app/backend/venv

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

systemctl enable slide-announcer-data-resize.service
systemctl enable slide-announcer-firstboot.service
systemctl enable slide-announcer-backend.service
systemctl enable slide-announcer-kiosk.service
systemctl enable nginx.service
systemctl enable seatd.service
EOF
