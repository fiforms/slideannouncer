#!/bin/bash
# Builds the 0.3.9 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# Retires the old mpv/DRM-takeover SRT video sink and switches nginx over
# to what its replacement needs. This hotfix is OS-level only — the
# matching backend/frontend changes (local-app/backend/srt_stream_bridge.py,
# an in-process asyncio task replacing the old daemon; the MSE player in
# frontend/src/srtStreamPlayer.js) ship through the app's own updater
# channel, same split the 0.3.3 audio-volume hotfix used.
#
# - /etc/systemd/system/slide-announcer-srt-sink.service +
#   /usr/local/sbin/slide-announcer-srt-sink-monitor: removed outright.
#   The new backend task already does everything this did (poll UDP 7002,
#   validate a candidate, gate on the same Settings > SRT Sink
#   passphrase/enable state) in-process, so leaving this unit running
#   would just have it fight the new backend task for the same UDP port.
#   script.sh disables the unit before deleting both files.
# - /etc/nginx/sites-available/slide-announcer.conf: the /api/ location
#   now proxies with the Upgrade/Connection headers
#   /api/local/srt-sink/stream (a WebSocket route) needs — inert for
#   every other plain-HTTP route under /api/.
# - /etc/nginx/conf.d/slide-announcer-websocket-upgrade.conf: new. The
#   $connection_upgrade map the line above depends on has to live at
#   nginx's http scope (conf.d, included there by the stock nginx.conf),
#   since `map` isn't valid inside that server block — see the file's own
#   comment for why this avoids just hardcoding `Connection: upgrade`
#   (would defeat keep-alive to the backend for every plain REST call).
#
# Requires a reboot after install: this hook can't safely reload nginx or
# restart a running unit from the live device shell it runs on (see
# make-hotfix-bundle.sh's own doc comment) — same "reboot to take effect"
# rule every prior hotfix touching a unit or nginx config has used.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="${HERE}/files"
SCRIPT="${HERE}/script.sh"

REQUIRED_VERSION="0.3.8"
NEW_VERSION="0.3.9"

"${HERE}/../../make-hotfix-bundle.sh" "$FILES_DIR" "$REQUIRED_VERSION" "$NEW_VERSION" "$SCRIPT"
