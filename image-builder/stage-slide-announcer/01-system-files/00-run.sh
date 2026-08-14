#!/bin/bash -e
# Installs system/, provisioning/, and local-app/ (staged into ./files/ by
# image-builder/build.sh before the pi-gen build starts) into the rootfs,
# creates the dedicated service user, and enables the units.

install -d "${ROOTFS_DIR}/opt/slide-announcer"
cp -r files/provisioning "${ROOTFS_DIR}/opt/slide-announcer/provisioning"

# The local-app self-updater (slide-announcer-local-app-updater.service) —
# fixed OS-image infra like provisioning/ above, deliberately outside the
# versioned /data/local-app/releases/<version>/ tree it manages, so a bad
# app release can never take the update mechanism itself down with it.
cp -r files/updater "${ROOTFS_DIR}/opt/slide-announcer/updater"

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

# Version stamp: this project's own semver (image-builder/VERSION,
# bumped manually per release), not a kernel/build-date/git-hash
# fingerprint — OTA bundles and hotfixes name themselves after this and
# gate on it (see make-hotfix-bundle.sh), so it needs to be something a
# human chose, not something that changes on every build. OS_VERSION
# comes from build.sh (host-side) via BUILD_INFO; BUILD_DATE/GIT_HASH are
# still in that file too, for build-log provenance only — see build.sh's
# own comment.
# shellcheck disable=SC1091
. files/BUILD_INFO
echo "${OS_VERSION:?}" > "${ROOTFS_DIR}/opt/slide-announcer/VERSION"

install -m 644 files/system/*.service files/system/*.timer "${ROOTFS_DIR}/etc/systemd/system/"
install -d "${ROOTFS_DIR}/usr/local/sbin" "${ROOTFS_DIR}/usr/local/bin"
install -m 755 files/system/scripts/data-resize.sh "${ROOTFS_DIR}/usr/local/sbin/slide-announcer-data-resize.sh"
install -m 755 files/system/scripts/kiosk-start.sh "${ROOTFS_DIR}/usr/local/bin/slide-announcer-kiosk-start.sh"
install -m 755 files/system/scripts/rauc-update.py "${ROOTFS_DIR}/usr/local/sbin/slide-announcer-update"
install -m 755 files/system/scripts/local-app-seed.py "${ROOTFS_DIR}/usr/local/sbin/slide-announcer-local-app-seed"
install -m 755 files/system/scripts/update-check.py "${ROOTFS_DIR}/usr/local/sbin/slide-announcer-update-check"
install -m 755 files/system/scripts/os-updater.py "${ROOTFS_DIR}/usr/local/sbin/slide-announcer-os-updater"
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

# Debian's rauc package (confirmed via `dpkg -L rauc`) ships only the
# binary — no rauc.service unit, no D-Bus service/policy files. Without
# these, `rauc install`/`rauc status` fail outright ("Failed to contact
# rauc service: The name de.pengutronix.rauc was not provided by any
# .service files"). Confirmed by testing TWICE now: first when this was
# discovered and fixed as a manual live patch directly on a device's
# filesystem, and again when an OTA-installed slot — a fresh copy from
# THIS build pipeline, never touched by that live patch — hit the exact
# same error, because the live patch was never captured here. These three
# files are RAUC's own documented D-Bus integration layout, not invented.
install -d "${ROOTFS_DIR}/usr/share/dbus-1/system-services" \
	"${ROOTFS_DIR}/etc/dbus-1/system.d" \
	"${ROOTFS_DIR}/etc/systemd/system"
cat > "${ROOTFS_DIR}/usr/share/dbus-1/system-services/de.pengutronix.rauc.service" <<'EOF'
[D-BUS Service]
Name=de.pengutronix.rauc
Exec=/usr/bin/rauc service
User=root
SystemdService=rauc.service
EOF
cat > "${ROOTFS_DIR}/etc/dbus-1/system.d/de.pengutronix.rauc.conf" <<'EOF'
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <policy user="root">
    <allow own="de.pengutronix.rauc"/>
  </policy>
  <policy context="default">
    <allow send_destination="de.pengutronix.rauc"/>
    <allow receive_sender="de.pengutronix.rauc"/>
  </policy>
</busconfig>
EOF
cat > "${ROOTFS_DIR}/etc/systemd/system/rauc.service" <<'EOF'
[Unit]
Description=Robust Auto-Update Controller (RAUC) service

[Service]
Type=dbus
BusName=de.pengutronix.rauc
ExecStart=/usr/bin/rauc service

[Install]
WantedBy=multi-user.target
EOF

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
#
# passno=0 (last field): a nonzero passno makes systemd's fstab-generator
# auto-create a systemd-fsck@....service unit ordered before data.mount,
# entirely independent of slide-announcer-factory-reset-check.service
# (which only declares Before=data.mount, not Before=systemd-fsck@...) —
# so nothing guarantees the reformat wins the race against that
# auto-generated fsck. Invisible on a genuinely blank card. Suspected (not
# yet confirmed root cause — needs a hands-on repro) to explain a real
# first-boot hang seen on a card reused from a previous, already-grown
# /data: fsck would trip on the stale, much-larger ext4 signature still
# sitting at this same on-disk offset, fail hard against the new (small,
# 128MB) partition size, and take data.mount down before
# factory-reset-check's mkfs ever runs — cascading into firstboot/
# local-app-seed/kiosk (all of which depend on /data), and since
# getty@tty1 is deliberately masked (the kiosk owns tty1), nothing is ever
# left to paint that display — just a permanently black screen until a
# manual power cycle gives the kernel a from-scratch partition scan.
# passno=0 removes the auto-fsck unit outright; ext4's journal replay on
# mount already covers unclean-shutdown recovery at the kernel level
# regardless of passno, and factory-reset-check.service's unconditional
# mkfs -F is already the sole authority over this partition's filesystem
# state on a flagged boot.
install -d "${ROOTFS_DIR}/data"
echo "DATADEV  /data  ext4  defaults,noatime,nofail  0  0" >> "${ROOTFS_DIR}/etc/fstab"

# Read-only rootfs (see SLIDE_ANNOUNCER.md, Tier 1, "Read-only rootfs"): the
# root filesystem itself is mounted ro (below), so anything that needs to
# write to /etc or /var at runtime goes through a CoW overlay instead.
# - /tmp, /var/tmp, /mnt/rauc: plain volatile tmpfs, nothing here needs to
#   survive a reboot. /mnt/rauc is where RAUC itself creates mount points
#   (bundle, rootfs.N) during an install — on a read-only root it can't
#   mkdir there at all (confirmed by testing: "Failed creating mount path
#   '/mnt/rauc/bundle': Read-only file system"), so this needs to be
#   writable the same way /tmp already is, not something RAUC-specific.
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
install -d "${ROOTFS_DIR}/mnt/rauc"
cat >> "${ROOTFS_DIR}/etc/fstab" <<'EOF'
tmpfs   /tmp        tmpfs    nosuid,nodev,mode=1777                                                                      0  0
tmpfs   /var/tmp    tmpfs    nosuid,nodev,mode=1777                                                                      0  0
tmpfs   /mnt/rauc   tmpfs    nosuid,nodev,mode=0700                                                                      0  0
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

# Repoint rpi-swap's "zram+file" backing file off its default /var/swap —
# /var's tmpfs upper means that file is backed by RAM, not disk (see
# system/read-only-root/rpi-swap-data.conf for the full story). Drop-in,
# not an edit to the vendor's /etc/rpi/swap.conf, per swap.conf(5)'s own
# recommendation.
install -d "${ROOTFS_DIR}/etc/rpi/swap.conf.d"
install -m 644 files/system/read-only-root/rpi-swap-data.conf \
	"${ROOTFS_DIR}/etc/rpi/swap.conf.d/slide-announcer-data.conf"

# cloud-init-local.service has no ordering against /etc's overlay mount by
# default (see system/read-only-root/cloud-init-etc-overlay.conf) — without
# this, a FACTORY_RESET boot can lose the NoCloud network-config's WiFi
# profile to a race and need a second reboot to pick it up.
install -d "${ROOTFS_DIR}/etc/systemd/system/cloud-init-local.service.d"
install -m 644 files/system/read-only-root/cloud-init-etc-overlay.conf \
	"${ROOTFS_DIR}/etc/systemd/system/cloud-init-local.service.d/slide-announcer-etc-overlay.conf"

# Quiet boot: move kernel/systemd console messages off tty1 (our kiosk's
# display) onto tty3 instead (still there via Ctrl+Alt+F3 for debugging,
# just not visible on the physical display), and suppress most kernel log
# lines and the blinking cursor. cmdline.txt is a single line — sed edits
# in place rather than risking reordering root=/rootfstype=/etc.
CMDLINE="${ROOTFS_DIR}/boot/firmware/cmdline.txt"
sed -i 's/console=tty1/console=tty3/' "$CMDLINE"
# Strip the stock "resize" cmdline token (pi-gen's stage2/01-sys-tweaks
# enables it) the same way rpi-resizerootfs.service is masked below —
# both exist for the classic Raspberry Pi OS first-boot "grow root to fill
# the SD card" flow, which this project replaces with repartition.sh
# sizing rootA at build time instead. Masking the service alone isn't
# enough: on a REAL device that already booted once, whatever consumes
# this token at runtime self-cleans it from the live cmdline.txt, but a
# RAUC bundle is built from a fresh, never-booted pi-gen image on the
# build host — that self-cleaning never runs there, so the bare "resize"
# token was riding straight into every OTA bundle's boot files untouched.
# Confirmed by testing: it caused a kernel panic ("Unable to mount root
# fs on unknown-block(0,0)") on the very first real tryboot attempt, on a
# rootB whose cmdline.txt still had it (rootA's own, from this project's
# very first real device boot, didn't — this bug was previously masked
# entirely by that one-time self-cleaning already having happened here).
sed -i -E 's/ resize\b//' "$CMDLINE"
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
# RAUC OTA/tryboot switching for free, more simply than it might sound:
# config.txt lives at the boot partition's shared top level, is NOT part
# of RAUC's per-slot "kernel" custom slot class (kernel/initramfs/.dtbs/
# overlays/cmdline.txt only — see system/rauc/system.conf), and is never
# touched by an OTA install or by rpi-tryboot-commit.sh's own commit step
# (which only flips config.txt's permanent os_prefix= line, added by
# repartition.sh after this script runs). So whatever's set here at build
# time simply stays, untouched, across every future OTA and every tryboot
# switch — there's nothing to keep in sync.
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

# labwc config dir for the kiosk session (see system/labwc/rc.xml for why
# this exists and what it blocks) — labwc reads $HOME/.config/labwc/rc.xml
# by default, and systemd sets HOME from the passwd entry above for
# slide-announcer-kiosk.service's User=slideannouncer. The rc.xml content
# itself is installed below, after this heredoc exits back to the host —
# "files/..." is a host-side path (this stage's own files/ dir), not
# reachable from inside the chroot this heredoc runs in.
install -d -o slideannouncer -g slideannouncer /var/lib/slide-announcer/.config
install -d -o slideannouncer -g slideannouncer /var/lib/slide-announcer/.config/labwc

# Lets the interactive `slideadmin` console/SSH account read
# /data/device-token (owner slideannouncer, mode 640 — see pairing.py's
# docstring) so `slide-announcer-update check`/`install`
# (system/scripts/rauc-update.py) can read the pairing token without sudo,
# the same way system/polkit/50-slide-announcer-system.rules already
# covers slideadmin for the reboot/tryboot side of that same CLI.
usermod -aG slideannouncer slideadmin

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

# Stock Debian periodic units that are pure waste on this device: /var is a
# tmpfs overlay (see system/README.md), so apt-daily/apt-daily-upgrade just
# re-fetch package indexes and .deb files from Debian's mirrors every day
# only to have them vanish on the next reboot — we never install via apt on
# a running device anyway (RAUC owns updates). man-db and dpkg-db-backup
# likewise churn tmpfs for a man-page index and a status backup nothing
# reads. e2scrub assumes a writable ext4 root; rootA/rootB are mounted ro.
# All enabled via vendor preset, not an explicit symlink, so `mask` (not
# `disable`) is what actually takes effect offline — same reasoning as
# getty@tty1 above.
systemctl mask apt-daily.timer apt-daily.service
systemctl mask apt-daily-upgrade.timer apt-daily-upgrade.service
systemctl mask man-db.timer man-db.service
systemctl mask dpkg-db-backup.timer dpkg-db-backup.service
systemctl mask e2scrub_all.timer e2scrub_all.service e2scrub_reap.service

systemctl enable slide-announcer-overlay-var-dirs.service
systemctl enable slide-announcer-factory-reset-check.service
systemctl enable slide-announcer-data-dirs.service
systemctl enable slide-announcer-rauc-dirs.service
systemctl enable slide-announcer-data-resize.service
systemctl enable slide-announcer-firstboot.service
systemctl enable slide-announcer-local-app-seed.service
systemctl enable slide-announcer-backend.service
systemctl enable slide-announcer-kiosk.service
systemctl enable slide-announcer-tryboot-check.service
systemctl enable slide-announcer-local-app-updater.timer
systemctl enable slide-announcer-os-updater.timer
systemctl enable rauc.service
systemctl enable nginx.service
systemctl enable seatd.service
EOF

# rc.xml content for the labwc config dir created above — mode 644 is
# world-readable, so root ownership here (this script itself runs as
# root, not as the chrooted slideannouncer user) is fine for labwc to
# read at kiosk-session start.
install -m 644 files/system/labwc/rc.xml \
	"${ROOTFS_DIR}/var/lib/slide-announcer/.config/labwc/rc.xml"
