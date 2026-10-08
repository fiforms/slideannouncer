#!/bin/bash
# Runs on-device after files/ is extracted (see make-hotfix-bundle.sh and
# hotfixes/README.md). $ROOT is the patched rootfs's bind-mount, not live /.
set -euo pipefail

# Sets the WiFi regulatory domain on the kernel cmdline. Images before 0.4.2
# relied on a top-level netplan "regulatory-domain" that is never applied under
# NetworkManager, so `iw reg get` stayed at the world domain (00).
# Substituted by build.sh from SLIDE_ANNOUNCER_WIFI_COUNTRY (image-builder/.env).
COUNTRY="@@WIFI_COUNTRY@@"
BOOTFW="/boot/firmware"

slide-announcer-bootfw-remount rw
trap 'slide-announcer-bootfw-remount ro' EXIT

# Both slots' cmdline.txt: a hotfix only patches the booted slot's rootfs, but
# the other slot's cmdline is the one a later tryboot would load.
for f in "$BOOTFW"/slotA/cmdline.txt "$BOOTFW"/slotB/cmdline.txt; do
	[ -f "$f" ] || continue
	if grep -q 'cfg80211\.ieee80211_regdom=' "$f"; then
		sed -i -E "s/cfg80211\.ieee80211_regdom=[A-Za-z0-9]{2}/cfg80211.ieee80211_regdom=${COUNTRY}/" "$f"
	else
		sed -i "s/\$/ cfg80211.ieee80211_regdom=${COUNTRY}/" "$f"
	fi
done

# Apply to the running kernel too, so the fix is visible before the reboot.
# Best-effort: the cmdline change above is what persists.
iw reg set "$COUNTRY" || true
