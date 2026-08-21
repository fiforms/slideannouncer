#!/bin/bash
# Sets the kiosk's HDMI output mode via wlr-randr, driven by
# pairing.read_screen_resolution() ("4k" or "1080p") — see
# local-app/backend/pairing.py's SCREEN_RESOLUTION_FILE and
# system_control.py's apply_screen_resolution(). Called both by
# kiosk-start.sh at boot (after labwc has started — wlr-randr needs a live
# compositor to talk to, unlike apply-audio-output.sh's PipeWire call which
# only needs the sound server up) and by the backend whenever Settings
# changes the value, so a switch takes effect without a kiosk restart.
#
# If a second HDMI output is connected, it's set to the same mode and
# positioned at the same origin as the first ("mirror") — see
# DISPLAY_IMPLEMENTATION.md for why mirroring, not extending, is the
# current default for a Pi 4's dual HDMI ports.
#
# NOT YET HARDWARE-TESTED — see DISPLAY_IMPLEMENTATION.md. In particular,
# the wlr-randr output names below (HDMI-A-1/HDMI-A-2) are the typical
# vc4-kms-v3d/wlroots naming but unconfirmed on this actual Pi, the same
# kind of assumption apply-audio-output.sh already documents for wpctl
# sink names.
#
# Never throws — a bad/missing mode or a wlr-randr version with a
# different flag set should leave the current display alone rather than
# blanking it. kiosk-start.sh already guards its own call with `|| true`,
# and system_control.apply_screen_resolution() doesn't check this script's
# exit code either — this is belt-and-suspenders on top of both.
set -uo pipefail

# wlr-randr talks to the compositor over the Wayland socket, which (like
# PipeWire's XDG_RUNTIME_DIR — see apply-audio-output.sh's comment on this
# exact class of bug) isn't inherited automatically by a caller outside
# labwc's own process tree. kiosk-start.sh exports XDG_RUNTIME_DIR before
# starting labwc, so that call is a no-op here. The OTHER caller,
# system_control.apply_screen_resolution(), runs inside
# slide-announcer-backend.service with no such session — without this,
# wlr-randr would fail to find a Wayland display at all.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}"

SCREEN_RESOLUTION_FILE="/data/status/screen-resolution"
TARGET="1080p"
if [ -f "$SCREEN_RESOLUTION_FILE" ]; then
	value="$(cat "$SCREEN_RESOLUTION_FILE" 2>/dev/null | tr -d '[:space:]')"
	if [ "$value" = "4k" ]; then
		TARGET="4k"
	fi
fi

if [ "$TARGET" = "4k" ]; then
	MODE="3840x2160@30Hz"
else
	MODE="1920x1080@60Hz"
fi

# wlr-randr may be asked to run before labwc has finished initializing its
# outputs — retry briefly rather than failing on the first check, same
# reasoning as apply-audio-output.sh's wpctl retry loop. Each attempt is
# capped with `timeout` for the same "don't block boot forever" reason
# that script documents for wpctl status.
OUTPUTS=""
for _ in 1 2 3 4 5; do
	OUTPUTS="$(timeout 3 wlr-randr 2>/dev/null | grep -oE '^[A-Za-z0-9_-]+' || true)"
	if [ -n "$OUTPUTS" ]; then
		break
	fi
	sleep 1
done

if [ -z "$OUTPUTS" ]; then
	echo "apply-screen-resolution: wlr-randr reported no outputs" >&2
	exit 1
fi

FIRST=""
FAILED=0
while IFS= read -r output; do
	[ -z "$output" ] && continue
	if [ -z "$FIRST" ]; then
		FIRST="$output"
		if ! wlr-randr --output "$output" --mode "$MODE" --pos 0,0 2>&1; then
			echo "apply-screen-resolution: failed to set $output to $MODE" >&2
			FAILED=1
		fi
	else
		# Mirror: same mode, same position as the first output.
		if ! wlr-randr --output "$output" --mode "$MODE" --pos 0,0 2>&1; then
			echo "apply-screen-resolution: failed to mirror $output at $MODE" >&2
			FAILED=1
		fi
	fi
done <<< "$OUTPUTS"

exit $FAILED
