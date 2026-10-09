#!/bin/bash
# Builds a "virt" test image from pi-gen's raw (boot + root) .img: the same
# rootfs as the real image, but bootable under UEFI on an arm64 VM (QEMU
# `-M virt` with AAVMF/EDK2, Proxmox with an emulated arm64 guest, or
# VirtualBox on an arm64 host) instead of on a Raspberry Pi. For testing the
# software on a workstation only — never shipped to a device.
#
#   make-virt-image.sh <raw.img> <out.img>
#
# Driven by `build.sh --virt` (which runs it after the real .img.xz/.raucb
# are done); it only READS raw.img, so the shipping artifacts are untouched.
#
# Layout (GPT, no rootB — there is no A/B here):
#   p1  bootfs  FAT32  EFI system partition. EFI/BOOT/BOOTAA64.EFI (GRUB),
#                      grub/grub.cfg, and the kernel + initramfs in slotA/
#                      (same place the Pi image keeps them). The Raspberry Pi
#                      firmware/kernel/dtb/config.txt files are removed.
#                      slideannouncer.yaml, network-config, etc. stay, so
#                      pre-provisioning works the same way.
#   p2  rootA   ext4   the rootfs, plus Debian's generic arm64 kernel (the
#                      Raspberry Pi kernels are purged — they lack the
#                      virtio drivers a VM needs) and an initramfs.
#   p3  data    ext4   partition-table entry only, formatted on first boot
#                      by the FACTORY_RESET flag and then grown to fill the
#                      disk, exactly as on the Pi image. Keep it LAST so
#                      growing the VM disk and rebooting just works.
#
# A/B/tryboot/RAUC updates can't work here: /opt/slide-announcer/VIRT_IMAGE
# is stamped into the rootfs, which the update units/scripts check (see
# system/scripts/os-updater.py, rauc-update.py and the Condition lines on the
# tryboot/os-updater units), and system.conf drops to a single slot with
# bootloader=noop.
#
# Needs root (loop devices, mount, chroot), network (apt, inside the chroot)
# and qemu-user binfmt for aarch64 (same prerequisite as build.sh itself).
set -euo pipefail

SRC_IMG="${1:?usage: make-virt-image.sh <raw.img> <out.img>}"
OUT_IMG="${2:?usage: make-virt-image.sh <raw.img> <out.img>}"

ESP_SIZE_MB="${VIRT_ESP_SIZE_MB:-256}"
# rootA = pi-gen's root content plus room for the generic kernel, its
# modules and the initramfs (the Pi kernels are purged, so this is generous).
ROOT_HEADROOM_MB="${VIRT_ROOT_HEADROOM_MB:-1536}"
DATA_SIZE_MB="${DATA_PLACEHOLDER_SIZE_MB:-128}"

if [ "$(id -u)" != "0" ]; then
	echo "make-virt-image.sh must run as root (loop devices, mount, chroot)" >&2
	exit 1
fi

QEMU_BIN="$(command -v qemu-aarch64-static || command -v qemu-aarch64 || true)"
if [ -z "$QEMU_BIN" ] && [ ! -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]; then
	echo "make-virt-image.sh: no qemu-aarch64 user emulation found — install qemu-user-binfmt (Ubuntu) or qemu-user-static (Debian)" >&2
	exit 1
fi

WORK_DIR="$(mktemp -d)"
SRC_LOOP=""
DST_LOOP=""
SRC_BOOT_MNT="${WORK_DIR}/src-boot"
SRC_ROOT_MNT="${WORK_DIR}/src-root"
ROOT_MNT="${WORK_DIR}/root"
mkdir -p "$SRC_BOOT_MNT" "$SRC_ROOT_MNT" "$ROOT_MNT"

cleanup() {
	set +e
	for m in dev/pts dev sys proc boot/firmware; do
		umount "${ROOT_MNT}/${m}" 2>/dev/null
	done
	umount "$ROOT_MNT" 2>/dev/null
	umount "$SRC_BOOT_MNT" 2>/dev/null
	umount "$SRC_ROOT_MNT" 2>/dev/null
	[ -n "$DST_LOOP" ] && losetup -d "$DST_LOOP" 2>/dev/null
	[ -n "$SRC_LOOP" ] && losetup -d "$SRC_LOOP" 2>/dev/null
	rm -rf "$WORK_DIR"
}
trap cleanup EXIT

# --- sizes --------------------------------------------------------------------
part_info="$(parted -ms "$SRC_IMG" unit B print)"
ROOT_SIZE_ACTUAL="$(echo "$part_info" | awk -F: '$1 == "2"' | cut -d: -f4 | tr -d B)"
ROOT_SIZE_MB=$(( (ROOT_SIZE_ACTUAL + 1024 * 1024 - 1) / (1024 * 1024) + ROOT_HEADROOM_MB ))

ESP_START_MB=1
ROOT_START_MB=$((ESP_START_MB + ESP_SIZE_MB))
DATA_START_MB=$((ROOT_START_MB + ROOT_SIZE_MB))
DATA_END_MB=$((DATA_START_MB + DATA_SIZE_MB))
# +1MiB tail for the GPT backup header/entries.
DISK_SIZE_MB=$((DATA_END_MB + 1))

echo "make-virt-image.sh: esp=${ESP_SIZE_MB}MiB rootA=${ROOT_SIZE_MB}MiB data(placeholder)=${DATA_SIZE_MB}MiB total=${DISK_SIZE_MB}MiB"

rm -f "$OUT_IMG"
truncate -s "${DISK_SIZE_MB}MiB" "$OUT_IMG"
parted --script "$OUT_IMG" mklabel gpt
parted --script "$OUT_IMG" unit MiB mkpart ESP fat32 "$ESP_START_MB" "$ROOT_START_MB"
parted --script "$OUT_IMG" set 1 esp on
parted --script "$OUT_IMG" unit MiB mkpart rootA ext4 "$ROOT_START_MB" "$DATA_START_MB"
parted --script "$OUT_IMG" unit MiB mkpart data ext4 "$DATA_START_MB" "$DATA_END_MB"

DST_LOOP="$(losetup --show --find --partscan "$OUT_IMG")"
SRC_LOOP="$(losetup --show --find --partscan --read-only "$SRC_IMG")"
udevadm settle 2>/dev/null || true

mkdosfs -n bootfs -F 32 -s 4 "${DST_LOOP}p1" > /dev/null
mkfs.ext4 -q -F -L rootA "${DST_LOOP}p2"
# p3 deliberately unformatted — see the header.

ESP_PARTUUID="$(blkid -s PARTUUID -o value "${DST_LOOP}p1")"
ROOTA_PARTUUID="$(blkid -s PARTUUID -o value "${DST_LOOP}p2")"
DATA_PARTUUID="$(blkid -s PARTUUID -o value "${DST_LOOP}p3")"

# --- copy the rootfs + boot partition ----------------------------------------
mount -o ro -t vfat "${SRC_LOOP}p1" "$SRC_BOOT_MNT"
mount -o ro -t ext4 "${SRC_LOOP}p2" "$SRC_ROOT_MNT"
mount -t ext4 "${DST_LOOP}p2" "$ROOT_MNT"
rsync -aHAXx --exclude /boot/firmware "${SRC_ROOT_MNT}/" "${ROOT_MNT}/"
mkdir -p "${ROOT_MNT}/boot/firmware"
mount -t vfat "${DST_LOOP}p1" "${ROOT_MNT}/boot/firmware"
BOOTFW="${ROOT_MNT}/boot/firmware"
rsync -rtx "${SRC_BOOT_MNT}/" "${BOOTFW}/"

# Derive the kernel command line from the Pi image's own (already carries the
# ro/quiet/wifi-regdom/console=tty3 edits from 00-run.sh), before the Pi files
# get removed below. serial0 is a Pi alias; a QEMU virt board's UART is
# ttyAMA0. The serial console goes LAST, which makes it /dev/console: initramfs
# and systemd messages (including any "can't find root" shell) then show up on
# the VM's serial port instead of invisibly on tty3.
CMDLINE="$(tr -d '\n' < "${BOOTFW}/cmdline.txt")"
CMDLINE="$(echo "$CMDLINE" | sed -E \
	-e 's/ ?console=serial0,[0-9]+//' \
	-e "s#root=PARTUUID=[0-9a-fA-F-]+#root=PARTUUID=${ROOTA_PARTUUID}#" \
	-e 's/ rauc\.slot=rootfs\.[01]//')"
CMDLINE="${CMDLINE} rauc.slot=rootfs.0 console=ttyAMA0,115200"

# --- rootfs edits that don't need the chroot ---------------------------------
# fstab: same by-mountpoint rewrite as the RAUC hook, since pi-gen's
# PARTUUIDs (and the DATADEV placeholder) belong to a different disk.
sed -i -E \
	-e "s#^\S+(\s+/\s)#PARTUUID=${ROOTA_PARTUUID}\1#" \
	-e "s#^\S+(\s+/boot/firmware\s)#PARTUUID=${ESP_PARTUUID}\1#" \
	-e "s#^\S+(\s+/data\s)#PARTUUID=${DATA_PARTUUID}\1#" \
	"${ROOT_MNT}/etc/fstab"

# Single slot, no bootloader integration: RAUC still starts and answers
# `rauc status`, but there's nothing to install into. Dropped: the custom
# backend handler, rootfs.1 and both kernel slots (their device paths won't
# exist here and rauc.service refuses to start over an unresolvable slot).
awk '
	/^\[/ { skip = ($0 ~ /^\[(handlers|slot\.rootfs\.1|slot\.kernel\.[01])\]/) }
	!skip { print }
' "${ROOT_MNT}/etc/rauc/system.conf" \
	| sed -e 's/^bootloader=custom/bootloader=noop/' \
		-e "s/@@ROOTA_PARTUUID@@/${ROOTA_PARTUUID}/" \
	> "${WORK_DIR}/system.conf"
install -m 644 "${WORK_DIR}/system.conf" "${ROOT_MNT}/etc/rauc/system.conf"

# The flag that blocks updates/tryboot (see the header).
echo "This is a virt (UEFI/VM) test image — no A/B slots, tryboot or OS updates." \
	> "${ROOT_MNT}/opt/slide-announcer/VIRT_IMAGE"

# --- chroot: generic kernel, initramfs, GRUB's arm64-efi modules -------------
if [ -n "$QEMU_BIN" ]; then
	install -m 755 "$QEMU_BIN" "${ROOT_MNT}/usr/bin/qemu-aarch64-static"
fi
mount -t proc proc "${ROOT_MNT}/proc"
mount -t sysfs sys "${ROOT_MNT}/sys"
mount --bind /dev "${ROOT_MNT}/dev"
mount --bind /dev/pts "${ROOT_MNT}/dev/pts"
# resolv.conf is often a dangling symlink inside an image (systemd-resolved /
# NetworkManager); swap in the host's for the duration of the apt run only.
mv "${ROOT_MNT}/etc/resolv.conf" "${ROOT_MNT}/etc/resolv.conf.virtbak" 2>/dev/null || true
cp -L /etc/resolv.conf "${ROOT_MNT}/etc/resolv.conf"

cat > "${ROOT_MNT}/embed.cfg" <<'EOF'
search --no-floppy --label bootfs --set=root
set prefix=($root)/grub
configfile $prefix/grub.cfg
EOF

cat > "${ROOT_MNT}/virt-chroot.sh" <<'EOF'
#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive LC_ALL=C

# MODULES=most BEFORE the kernel installs: the default "dep" only packs the
# drivers this (container) build environment needs, which leaves out
# virtio_pci/virtio_blk — the initramfs then can't find the root disk.
echo "MODULES=most" > /etc/initramfs-tools/conf.d/virt-modules.conf

apt-get update
# Install the generic kernel FIRST so there is always a bootable kernel, then
# purge the Raspberry Pi ones. grub-efi-arm64-bin (modules only) rather than
# grub-efi-arm64: that package's postinst tries to grub-install into NVRAM.
apt-get install -y --no-install-recommends initramfs-tools linux-image-arm64 grub-efi-arm64-bin

# Pi OS may ship initramfs generation switched off (the Pi boots without
# one); a VM needs it for virtio/ext4 modules.
if [ -f /etc/initramfs-tools/update-initramfs.conf ]; then
	sed -i 's/^update_initramfs=.*/update_initramfs=yes/' /etc/initramfs-tools/update-initramfs.conf
fi
KVER="$(ls /boot/vmlinuz-*-arm64 | sed 's#/boot/vmlinuz-##' | sort -V | tail -n1)"
[ -f "/boot/initrd.img-${KVER}" ] || update-initramfs -c -k "$KVER"

pi_kernels="$(dpkg-query -W -f='${Package}\n' 'linux-image-*' 'linux-headers-*' 2>/dev/null | grep -E 'rpi|rpt' || true)"
if [ -n "$pi_kernels" ]; then
	# shellcheck disable=SC2086
	apt-get purge -y $pi_kernels || echo "warning: could not purge Pi kernel packages" >&2
fi

# A UEFI removable-media loader with an embedded config that finds the ESP by
# its FAT label — no grub-probe/NVRAM dependence, so it boots on any firmware.
grub-mkimage -O arm64-efi -o /BOOTAA64.EFI -p /grub -c /embed.cfg \
	part_gpt part_msdos fat ext2 normal configfile linux search search_label \
	echo test gzio efi_gop all_video

# Pi-only: checks for Raspberry Pi EEPROM updates and just fails in a VM.
systemctl mask rpi-eeprom-update.service || true

apt-get clean
rm -rf /var/lib/apt/lists/*
echo "$KVER" > /virt-kver
EOF
chmod 755 "${ROOT_MNT}/virt-chroot.sh"
chroot "$ROOT_MNT" /virt-chroot.sh

KVER="$(cat "${ROOT_MNT}/virt-kver")"

# Restore the image's own resolv.conf, drop the chroot scaffolding.
rm -f "${ROOT_MNT}/etc/resolv.conf"
mv "${ROOT_MNT}/etc/resolv.conf.virtbak" "${ROOT_MNT}/etc/resolv.conf" 2>/dev/null || true
rm -f "${ROOT_MNT}/virt-chroot.sh" "${ROOT_MNT}/virt-kver" "${ROOT_MNT}/embed.cfg" \
	"${ROOT_MNT}/usr/bin/qemu-aarch64-static"

# --- turn the boot partition into the ESP ------------------------------------
# Whatever the kernel hooks put on it during the chroot above, plus the Pi
# firmware/kernel/dtb/config files, goes; slideannouncer.yaml and
# network-config (pre-provisioning) are left alone.
find "$BOOTFW" -mindepth 1 -maxdepth 1 \( \
	-name 'start*.elf' -o -name 'fixup*.dat' -o -name 'bootcode.bin' \
	-o -name 'config.txt' -o -name 'cmdline.txt' -o -name 'tryboot.txt' \
	-o -name '*.dtb' -o -name 'overlays' -o -name 'kernel*.img' \
	-o -name 'initramfs*' -o -name 'slotA' -o -name 'slotB' \
	-o -name 'state-[AB]' -o -name 'LICENCE.broadcom' -o -name 'COPYING.linux' \
	-o -name 'issue.txt' -o -name '*.bin' -o -name 'EFI' -o -name 'grub' \
	\) -exec rm -rf {} +

mkdir -p "${BOOTFW}/EFI/BOOT" "${BOOTFW}/grub" "${BOOTFW}/slotA"
mv "${ROOT_MNT}/BOOTAA64.EFI" "${BOOTFW}/EFI/BOOT/BOOTAA64.EFI"
cp "${ROOT_MNT}/boot/vmlinuz-${KVER}" "${BOOTFW}/slotA/vmlinuz"
cp "${ROOT_MNT}/boot/initrd.img-${KVER}" "${BOOTFW}/slotA/initrd.img"
cat > "${BOOTFW}/grub/grub.cfg" <<EOF
set default=0
set timeout=0
linux /slotA/vmlinuz ${CMDLINE}
initrd /slotA/initrd.img
boot
EOF
echo "$CMDLINE" > "${BOOTFW}/slotA/cmdline.txt"

# Same as repartition.sh: the first boot formats /data.
touch "${BOOTFW}/FACTORY_RESET"

sync
umount "${ROOT_MNT}/dev/pts" "${ROOT_MNT}/dev" "${ROOT_MNT}/sys" "${ROOT_MNT}/proc"
umount "$BOOTFW"
umount "$ROOT_MNT"
umount "$SRC_BOOT_MNT"
umount "$SRC_ROOT_MNT"
e2fsck -fy "${DST_LOOP}p2" || true
losetup -d "$DST_LOOP"
losetup -d "$SRC_LOOP"
DST_LOOP=""
SRC_LOOP=""

echo "make-virt-image.sh: wrote ${OUT_IMG} (kernel ${KVER})"
echo "  esp   PARTUUID=${ESP_PARTUUID}  label bootfs"
echo "  rootA PARTUUID=${ROOTA_PARTUUID}"
echo "  data  PARTUUID=${DATA_PARTUUID}  (formatted on first boot, then grows with the disk)"
echo "  boot with UEFI firmware (arm64), e.g.:"
echo "    qemu-system-aarch64 -M virt -cpu cortex-a72 -m 4G -smp 4 -bios /usr/share/AAVMF/AAVMF_CODE.fd \\"
echo "      -drive file=${OUT_IMG},format=raw,if=virtio -device virtio-gpu-pci -device qemu-xhci -device usb-kbd -nic user"
