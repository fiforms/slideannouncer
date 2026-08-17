#!/bin/bash
# Creates /data/home/slideadmin (the persistent backing store for the
# slideadmin console/SSH account's home dir, bind-mounted over
# /home/slideadmin — see 00-run.sh's fstab entry and system/README.md).
# Runs every boot, before that bind mount, same shape as
# slide-announcer-data-dirs.service: a factory reset wipes /data at
# runtime, which would otherwise leave this directory missing on the very
# next boot.
#
# Seeded from /etc/skel (not from whatever's currently at /home/slideadmin
# on rootfs) so this doesn't depend on running before/after any other
# mutation of that path, and so a factory reset regenerates the same
# starting dotfiles a brand-new device gets. Only seeded once — an
# existing /data/home/slideadmin (a device that already has real history/
# state in it) is never overwritten.
set -euo pipefail

HOME_DIR=/data/home/slideadmin

if [ ! -e "$HOME_DIR" ]; then
	mkdir -p "$HOME_DIR"
	cp -a /etc/skel/. "$HOME_DIR/"
fi

chown -R slideadmin:slideadmin "$HOME_DIR"
chmod 0700 "$HOME_DIR"
