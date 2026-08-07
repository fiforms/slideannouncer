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
apt-get clean
rm -rf /var/lib/apt/lists/*
rm -f /root/.bash_history /home/*/.bash_history
find /var/log -type f -exec truncate -s 0 {} \;
EOF

rm -rf "${ROOTFS_DIR}"/tmp/* "${ROOTFS_DIR}"/var/tmp/*
