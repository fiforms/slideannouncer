#!/bin/bash -e
# Sanitize step before pi-gen exports/compresses the image. Nothing here
# should ever be shipped identifiable/reusable across devices — SSH host
# keys and machine-id are regenerated for real by provisioning/firstboot.py
# on the device's actual first boot instead.
#
# The RAUC signing key is never baked in here (or anywhere) — bundle
# signing is Tier 1 OTA work, not yet built (see SLIDE_ANNOUNCER.md).

rm -f "${ROOTFS_DIR}"/etc/ssh/ssh_host_*
true > "${ROOTFS_DIR}/etc/machine-id"
rm -f "${ROOTFS_DIR}/var/lib/dbus/machine-id"
ln -sf /etc/machine-id "${ROOTFS_DIR}/var/lib/dbus/machine-id"

on_chroot << 'EOF'
# WiFi firmware for chipsets this fleet never uses. The Pi 4/5 onboard radio
# is Broadcom/Cypress (brcmfmac, part of raspi-firmware/linux-firmware, not
# these packages) — Atheros/Realtek/MediaTek are only relevant to USB WiFi
# dongles nobody's using here. Plain `apt-get purge` (not path-exclude) so
# that if something unexpectedly hard-depends on one of these, the build
# fails loudly here instead of shipping a silently broken image.
apt-get purge -y firmware-atheros firmware-realtek firmware-mediatek

# Single-language (English) kiosk — every other Chromium locale pak is
# dead weight. Deleting the files rather than purging the package: chromium
# itself likely depends on chromium-l10n, and nothing on this read-only,
# apt-never-runs-again image will ever touch dpkg's package database for it
# again anyway. dpkg -L gives the exact on-disk paths straight from the
# installed package's own manifest instead of guessing them.
dpkg -L chromium-l10n \
	| grep -E '\.pak$' \
	| grep -v -E '/(en-US|en-GB)\.pak$' \
	| xargs -r rm -f

# Man pages, docs, and non-English locale data: nothing on this device ever
# reads them (man-db itself is masked in 01-system-files/00-run.sh, and
# there's no interactive shell workflow in normal operation).
rm -rf /usr/share/man/* /usr/share/doc/*
find /usr/share/locale -maxdepth 1 -mindepth 1 ! -name 'en*' ! -name locale.alias -exec rm -rf {} +

# Build toolchain: nothing on the device ever compiles anything again after
# this image is built — RAUC ships prebuilt bundles, and requirements.txt
# (fastapi/uvicorn/httpx) has aarch64 wheels for everything, so the venv pip
# install in 01-system-files/00-run.sh shouldn't need a compiler either.
# Driven off `apt-mark showauto` rather than a fixed list, since the exact
# package names carry the gcc/kernel version and would go stale on the next
# bump — but excluding anything ending `-base` is load-bearing, not
# cosmetic: gcc-14-base looks like a toolchain package by name but is a
# real runtime dependency of libgcc-s1 (required by ~everything, including
# apt itself). A naive 'gcc-*' glob purge confirmed this live on real
# hardware — it tried to remove gcc-14-base and broke apt's own dependency
# resolution before a single package was actually removed.
TOOLCHAIN_PKGS="$(apt-mark showauto \
	| grep -E '^(gcc|g\+\+|cpp)(-[0-9]|-aarch64|$)|^linux-headers-' \
	| grep -vE -- '-base$' || true)"
echo "toolchain packages queued for purge:"
echo "${TOOLCHAIN_PKGS:-<none>}"
if [ -n "$TOOLCHAIN_PKGS" ]; then
	# shellcheck disable=SC2086
	apt-get purge -y $TOOLCHAIN_PKGS
fi
apt-get purge -y libpython3-dev libpython3.13-dev
apt-get autoremove --purge -y

apt-get clean
rm -rf /var/lib/apt/lists/*
rm -f /root/.bash_history /home/*/.bash_history
find /var/log -type f -exec truncate -s 0 {} \;
EOF

rm -rf "${ROOTFS_DIR}"/tmp/* "${ROOTFS_DIR}"/var/tmp/*
