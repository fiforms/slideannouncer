#!/bin/bash
# Runs on-device after files/ is extracted (see make-hotfix-bundle.sh and
# hotfixes/README.md).
#
# 1. Two new on-demand systemd units just landed under /etc/systemd/system/
#    (slide-announcer-os-updater-now.service,
#    slide-announcer-local-app-updater-now.service) — systemd caches its
#    unit list and won't see them without an explicit reload, same as
#    hotfixes/0.1.8/script.sh's tryboot unit.
#
# 2. Corrects this device's own /etc/rauc/system.conf. Every device that's
#    taken a second full-OS OTA ends up with stale
#    [slot.rootfs.0]/[slot.rootfs.1] device= PARTUUIDs in it — baked in by
#    repartition.sh at whatever release *built* the rootfs currently
#    running, not this device's own disk (fstab/cmdline.txt don't have
#    this problem because the RAUC install hook already re-derives those
#    two via blkid on the real device — system.conf just never got the
#    same treatment). image-builder/build.sh's hook.sh now does this for
#    every future OTA install; this hotfix applies the identical fix
#    directly to whatever's already on disk, so devices don't have to wait
#    for a third OTA to get a correct system.conf. Same by-LOCATION sed
#    (each PARTUUID substituted only within its own [slot.rootfs.N]
#    stanza) as the hook, and the same blkid -L rootA/rootB source of
#    truth — both partitions already carry valid, correctly-labeled
#    filesystems on any device that's booted successfully at all.
#
#    $ROOT (the bind-mounted, non-overlaid real rootfs — see
#    make-hotfix-bundle.sh's own comment) is used here rather than the
#    live /etc/rauc/system.conf path directly, per hotfixes/README.md's
#    convention — though since nothing has ever written through the /etc
#    overlay's upper layer for this file, editing it here is what the live
#    path already transparently reads from anyway.
#
#    rauc.service itself only reads system.conf at its own startup, not on
#    a live watch — restarted here so the fix is actually in effect
#    immediately, without waiting for this device's next reboot.
set -euo pipefail

systemctl daemon-reload

ROOTA_PARTUUID="$(blkid -s PARTUUID -o value "$(blkid -L rootA)")"
ROOTB_PARTUUID="$(blkid -s PARTUUID -o value "$(blkid -L rootB)")"
sed -i -E \
	-e "/^\[slot\.rootfs\.0\]/,/^\[/{s#^device=/dev/disk/by-partuuid/[0-9a-fA-F-]+#device=/dev/disk/by-partuuid/${ROOTA_PARTUUID}#}" \
	-e "/^\[slot\.rootfs\.1\]/,/^\[/{s#^device=/dev/disk/by-partuuid/[0-9a-fA-F-]+#device=/dev/disk/by-partuuid/${ROOTB_PARTUUID}#}" \
	"${ROOT:?}/etc/rauc/system.conf"

systemctl restart rauc.service
