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

CHROMIUM_CMD=(
	chromium
	--kiosk
	--ozone-platform=wayland
	--noerrdialogs
	--disable-infobars
	--disable-session-crashed-bubble
	--check-for-update-interval=31536000
	# Chromium's first compositor frame is white by default, painted before
	# the page (whose own CSS is already all-dark) has loaded far enough to
	# override it — a brief flash that's jarring on a large TV. This switch
	# changes that pre-paint default so the flash is black instead of white.
	--default-background-color=000000
	--app=http://localhost/kiosk
)

exec labwc -s "${CHROMIUM_CMD[*]}"
