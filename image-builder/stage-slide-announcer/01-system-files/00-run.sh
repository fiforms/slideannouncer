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

# Placeholder fstab entry — image-builder/repartition.sh rewrites DATADEV to
# the real PARTUUID of partition 4 once the final partition table exists
# (pi-gen itself only ever produces boot+root, see repartition.sh).
echo "DATADEV  /data  ext4  defaults,noatime,nofail  0  2" >> "${ROOTFS_DIR}/etc/fstab"

# Quiet boot: move kernel/systemd console messages off tty1 (our kiosk's
# display) onto tty3 instead (still there via Ctrl+Alt+F3 for debugging,
# just not visible on the physical display), and suppress most kernel log
# lines and the blinking cursor. cmdline.txt is a single line — sed edits
# in place rather than risking reordering root=/rootfstype=/etc.
CMDLINE="${ROOTFS_DIR}/boot/firmware/cmdline.txt"
sed -i 's/console=tty1/console=tty3/' "$CMDLINE"
sed -i 's/$/ quiet loglevel=3 logo.nologo vt.global_cursor_default=0/' "$CMDLINE"
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
