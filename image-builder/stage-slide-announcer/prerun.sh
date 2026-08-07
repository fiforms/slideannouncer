#!/bin/bash -e
# Every pi-gen stage needs this — seeds this stage's rootfs from the
# previous stage's finished one (stage2's Lite base) before our own
# substeps (00-packages, 01-system-files, 02-clean-before-compress) run
# against it. Without it ROOTFS_DIR starts empty.
if [ ! -d "${ROOTFS_DIR}" ]; then
	copy_previous
fi
