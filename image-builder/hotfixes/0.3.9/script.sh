#!/bin/bash
# Runs on-device after files/ is extracted (see make-hotfix-bundle.sh and
# hotfixes/README.md). $ROOT is the patched rootfs's bind-mount, not live /.
set -euo pipefail

# Retires the old mpv/DRM-takeover SRT sink daemon — replaced by
# local-app/backend/srt_stream_bridge.py, an in-process asyncio task in
# the backend (ships via the local-app updater channel, not this hotfix;
# see build.sh). Left running, this unit would keep binding UDP 7002
# alongside the new backend task's own poll socket, racing it for the
# same port. `disable` first (while the unit file's [Install] section is
# still there to resolve the right symlink), then remove the file and its
# script — same order slide-announcer-rauc.conf's retirement used in the
# 0.1.4 hotfix.
systemctl --root="$ROOT" disable slide-announcer-srt-sink.service
rm -f "$ROOT/etc/systemd/system/slide-announcer-srt-sink.service"
rm -f "$ROOT/usr/local/sbin/slide-announcer-srt-sink-monitor"
