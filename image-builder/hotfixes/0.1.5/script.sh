#!/bin/bash
# Runs on-device after files/ is extracted (see make-hotfix-bundle.sh and
# hotfixes/README.md). Supersedes this hotfix's first, never-completed
# attempt, which patched /etc/dphys-swapfile — a package that turned out
# not to be installed on this image at all. The device's real swap
# mechanism is rpi-swap (zram + file-backed overflow), configured at
# /etc/rpi/swap.conf(.d/), and its /var/swap-backed file half had the
# identical RAM-not-disk bug (see system/README.md).
#
# Every systemctl/swap call below is best-effort (`|| true`): the
# guaranteed part of this hotfix is the corrected config file, which takes
# effect cleanly on the device's next boot via rpi-swap-generator
# regardless of what happens here. Live reactivation is a bonus, not
# something worth risking the whole hotfix (including the VERSION bump)
# over if this device's swap chain doesn't tear down and restart cleanly
# while running.
set -euo pipefail

# /var/swap lives on /var's tmpfs upper — every byte rpi-swap ever wrote to
# it is RAM, not disk. Tear the old chain down and free it now rather than
# waiting for a reboot: stop the swap device, then the loop device holding
# the file open (its ExecStop detaches the loop device), then remove the
# file itself.
swapoff /dev/zram0 2>/dev/null || true
systemctl stop rpi-setup-loop@var-swap.service 2>/dev/null || true
rm -f /var/swap

# files/ just landed the new /etc/rpi/swap.conf.d/slide-announcer-data.conf
# (Path=/data/swap) onto the real rootfs, which the /etc overlay has no
# shadow of yet, so it's already visible at the live path too. Reload so
# rpi-swap-generator regenerates its unit chain against the new Path=,
# then restart the zram side to pull that corrected chain back up live.
systemctl daemon-reload 2>/dev/null || true
systemctl restart systemd-zram-setup@zram0.service 2>/dev/null || true
