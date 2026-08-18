#!/bin/bash
# Starts the labwc Wayland compositor with Chromium autostarted in kiosk
# mode against the locally-served slideshow page. Runs as the dedicated
# `slideannouncer` user via seatd (no logind session/graphical login involved).
set -euo pipefail

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

export WLR_LIBINPUT_NO_DEVICES=1
export WLR_SEATD=1

# This theme (installed by 01-system-files' 00-run.sh to
# /usr/share/icons/slide-announcer-blank) is the entire Adwaita cursor set
# with only cursors/default overwritten by a fully transparent 1x1 image,
# so the idle arrow is invisible from the very first compositor frame
# without waiting on a real input event — unlike Chromium's CSS
# `cursor: none` (Slideshow.vue), which only ever took effect after a
# genuine pointer event. Every other shape (hover, text, busy, resize, …)
# is a real Adwaita cursor, not left undefined — confirmed on real hardware
# that a name this theme doesn't define at all renders as nothing rather
# than falling back to Chromium's own cursors, so link hover/text fields on
# Settings/Pairing would otherwise be invisible too. A real mouse still
# moves/clicks normally throughout; only the idle arrow is invisible.
export XCURSOR_THEME=slide-announcer-blank
export XCURSOR_SIZE=24

# Audio session — there's no logind/systemd user session here (see this
# script's own docstring above), so PipeWire/WirePlumber can't start the
# normal way (systemd --user units activated by a login session). Instead
# they're plain background processes under the same manually-built
# XDG_RUNTIME_DIR Chromium itself will use, so pipewire-pulse's socket is
# where Chromium's PulseAudio-protocol audio backend expects to find it.
# Backgrounded before the `exec` below, so they stay in this same process's
# cgroup and systemd tears them down with the rest of the unit on stop,
# same as everything else this script launches.
pipewire &
wireplumber &
pipewire-pulse &
sleep 1
/usr/local/sbin/slide-announcer-apply-audio-output || true

CHROMIUM_CMD=(
	chromium
	--kiosk
	--ozone-platform=wayland
	--noerrdialogs
	--disable-infobars
	--disable-session-crashed-bubble
	--check-for-update-interval=31536000
	# No one is ever present to click on this kiosk display, so Chromium's
	# normal "needs a user gesture (or high site media-engagement) before
	# allowing unmuted autoplay" policy would otherwise force every video
	# slide to play silently forever. This is the only reliable way to get
	# sound out of video slides here.
	--autoplay-policy=no-user-gesture-required
	# Chromium's first compositor frame is white by default, painted before
	# the page (whose own CSS is already all-dark) has loaded far enough to
	# override it — a brief flash that's jarring on a large TV. This switch
	# changes that pre-paint default so the flash is black instead of white.
	--default-background-color=000000
	--app=http://localhost/kiosk
)

exec labwc -s "${CHROMIUM_CMD[*]}"
