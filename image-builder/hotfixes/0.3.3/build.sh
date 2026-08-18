#!/bin/bash
# Builds the 0.3.3 hotfix bundle. See hotfixes/README.md for the convention
# this directory follows.
#
# Captures the remote's volume/mute keys and drives PipeWire's volume via
# wpctl, mirroring the 0.3.1 power-button hotfix's shape exactly:
#
# - /usr/local/sbin/slide-announcer-volume-key-monitor +
#   /etc/systemd/system/slide-announcer-volume-key.service: capability-based
#   evdev listener (same discovery pattern as
#   slide-announcer-power-button-monitor) for KEY_VOLUMEUP/KEY_VOLUMEDOWN/
#   KEY_MUTE — confirmed via Settings > Key Debug as AudioVolumeUp (175)/
#   AudioVolumeDown (174)/AudioVolumeMute (173). Adjusts in 5% steps
#   (0-100), honors autorepeat while held, auto-unmutes on a volume press,
#   calls wpctl directly, and persists to /data/status/audio-volume /
#   /data/status/audio-muted. Runs unprivileged as slideannouncer, own
#   unit, independent of the kiosk — same reasoning as the power-button
#   unit: the remote should still work while the kiosk is asleep/restarting.
#   script.sh enables the new unit — a plain file drop doesn't create the
#   WantedBy= symlink on its own.
# - /usr/local/sbin/slide-announcer-apply-audio-output: extended to
#   re-apply the persisted volume/mute right after selecting the default
#   sink, so a boot or an output switch (Settings > System > Audio Output)
#   doesn't drop back to PipeWire's own ~40% default.
#
# This hotfix is OS-level only — the corresponding local-app changes
# (read-only /api/local/audio-volume endpoint, the on-screen level/mute
# indicator in Slideshow.vue) ship through the app's own updater channel,
# not this OS hotfix mechanism. See AUDIO_IMPLEMENTATION.md for the full
# feature writeup.
#
# Revised after a first real-hardware install: the remote and the
# indicator both worked, but nothing audible actually changed, and the
# same was true of the pre-existing Settings > Audio Output HDMI/Headphone
# Jack switch (it moved the on-screen selection but never re-routed
# sound). Root cause in both cases: PipeWire/WirePlumber run under the
# XDG_RUNTIME_DIR kiosk-start.sh exports before backgrounding them, but
# neither slide-announcer-volume-key.service nor the wpctl calls
# system_control.apply_audio_output() makes (via
# slide-announcer-backend.service) have a systemd --user session to
# inherit that from — so every wpctl call silently talked to no PipeWire
# instance at all (nothing checks its exit code, so this failed silently
# rather than erroring). Fixed in both
# slide-announcer-volume-key-monitor (sets XDG_RUNTIME_DIR itself, same
# computation as kiosk-start.sh) and slide-announcer-apply-audio-output
# (same fix, since that script is also what runs
# system_control.apply_audio_output()'s wpctl calls) — see either file's
# own comment.
#
# Requires a reboot after install for the new unit to take effect (same as
# 0.3.1/0.2.8).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="${HERE}/files"
SCRIPT="${HERE}/script.sh"

REQUIRED_VERSION="0.3.2"
NEW_VERSION="0.3.3"

"${HERE}/../../make-hotfix-bundle.sh" "$FILES_DIR" "$REQUIRED_VERSION" "$NEW_VERSION" "$SCRIPT"
