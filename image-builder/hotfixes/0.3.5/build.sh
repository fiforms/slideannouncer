#!/bin/bash
# Builds the 0.3.5 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# Fixes the SRT video sink's real-hardware playback problems (high CPU,
# growing audio/video lag, no sound, hardcoded 1080p output) found while
# testing it on a 4K TV. Three files change, all OS-level
# (system/scripts/, system/*.service) — the matching local-app changes
# (the /api/local/srt-sink/playing endpoint and Slideshow.vue's pause-
# while-external-playback wiring) ship through the app's own updater
# channel, not this OS hotfix, same split 0.3.3 used for its audio-volume
# feature.
#
# - /usr/local/sbin/slide-announcer-srt-sink-monitor: the original
#   direct-DRM/KMS mpv invocation (system/scripts/srt-sink-monitor.py's
#   own module docstring has the full before/after) had --video-sync=desync
#   plus --untimed/--no-correct-pts, which let the decode loop free-run
#   instead of pacing to realtime — confirmed on hardware to peg the CPU
#   and grow A-V drift past 30s with no bound once decode ever dipped
#   below the stream's real-time rate. Now: mpv runs as a fullscreen
#   Wayland client inside the kiosk's already-running labwc compositor
#   (--gpu-context=wayland) instead of taking DRM/KMS directly and
#   stopping the kiosk first — this fixes CPU/lag (no more forced
#   software scale/blit when the stream's resolution doesn't match a
#   hardcoded --drm-mode; the compositor scales it, the same path
#   Chromium's own 4K rendering already uses), removes the 1080p
#   hardcode entirely, and fixes no-sound reports (--ao=pipewire routes
#   through the same audio graph slide-announcer-apply-audio-output
#   already configures for the kiosk, instead of a bare --ao=alsa
#   opening whatever ALSA considers its default device). Also adds
#   verbose journal logging around the whole play path (previously
#   --really-quiet) so a future regression is diagnosable from
#   `journalctl -u slide-announcer-srt-sink` instead of invisible.
# - /usr/local/sbin/slide-announcer-display-power: drops the `takeover`
#   action — no longer called now that the kiosk is left running (only
#   woken if it was asleep) rather than stopped for external playback.
# - /etc/systemd/system/slide-announcer-srt-sink.service: drops `audio`
#   from SupplementaryGroups (playback no longer opens ALSA directly).
#
# Not yet hardware-tested for correct fullscreen stacking above
# Chromium's own fullscreen kiosk surface in labwc, or for whether
# --hwdec=v4l2m2m (switched from v4l2m2m-copy, for a zero-copy dmabuf
# path into the compositor) actually negotiates cleanly on this driver
# stack — see srt-sink-monitor.py's own docstring for both caveats.
#
# Requires a reboot after install: slide-announcer-srt-sink.service is
# already enabled from the base image, but this hook can't safely reload/
# restart a running unit from the live device shell it runs on (see
# make-hotfix-bundle.sh's own doc comment) — same "reboot to take effect"
# rule the 0.3.1/0.3.3 hotfixes used for their new units.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="${HERE}/files"

REQUIRED_VERSION="0.3.4"
NEW_VERSION="0.3.5"

"${HERE}/../../make-hotfix-bundle.sh" "$FILES_DIR" "$REQUIRED_VERSION" "$NEW_VERSION"
