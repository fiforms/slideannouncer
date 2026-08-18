#!/bin/bash
# Points PipeWire's default sink at whichever output
# pairing.read_audio_output() currently says ("hdmi" or "headphones") —
# see local-app/backend/pairing.py's AUDIO_OUTPUT_FILE and
# system_control.py's apply_audio_output(). Called both by
# kiosk-start.sh at boot (after backgrounding pipewire/wireplumber/
# pipewire-pulse there) and by the backend whenever the Settings UI
# changes the value, so a switch takes effect without a kiosk restart.
#
# Never throws — a missing/renamed ALSA sink should leave whatever output
# was already active alone rather than taking down the caller.
# kiosk-start.sh already guards its own call with `|| true`, and
# system_control.apply_audio_output() doesn't check this script's exit
# code either — this is belt-and-suspenders on top of both.
set -uo pipefail

AUDIO_OUTPUT_FILE="/data/status/audio-output"
TARGET="hdmi"
if [ -f "$AUDIO_OUTPUT_FILE" ]; then
	value="$(cat "$AUDIO_OUTPUT_FILE" 2>/dev/null | tr -d '[:space:]')"
	if [ "$value" = "headphones" ]; then
		TARGET="headphones"
	fi
fi

# wpctl/PipeWire may still be coming up (kiosk-start.sh only sleeps 1s
# after backgrounding them) — retry briefly rather than failing on the
# first check.
#
# Restricted to the Audio section's "Sinks:" block specifically — not
# "Devices:" (a separate ID namespace `wpctl set-default` doesn't accept,
# and can contain the same substring as its sink), and not Video's own
# (always-empty in practice, but structurally identical) "Sinks:" label
# further down the same `wpctl status` output.
#
# Confirmed on real hardware (a Pi 4 with vc4-kms-v3d) that neither sink is
# actually named "Headphones" — both show up as "Built-in Audio", with
# only the HDMI one distinguishable by a literal "(HDMI)" suffix:
#   64. Built-in Audio Stereo
#   65. Built-in Audio Digital Stereo (HDMI)
# So "hdmi" is matched by substring (safe regardless of exact ALSA naming
# variance), and "headphones" is matched as "whichever Built-in Audio sink
# is NOT the HDMI one" rather than by any literal name — there may not be
# one to match on other hardware revisions either.
SINK_ID=""
for _ in 1 2 3 4 5; do
	AUDIO_SINKS="$(wpctl status 2>/dev/null \
		| awk '/^Audio$/{in_audio=1} /^Video$/{in_audio=0} in_audio && /Sinks:/{flag=1; next} in_audio && /(├─|└─)/{flag=0} flag')"
	if [ "$TARGET" = "headphones" ]; then
		SINK_ID="$(echo "$AUDIO_SINKS" | grep -i "built-in audio" | grep -vi "hdmi" | grep -oE '[0-9]+' | head -n1)"
	else
		SINK_ID="$(echo "$AUDIO_SINKS" | grep -i "hdmi" | grep -oE '[0-9]+' | head -n1)"
	fi
	if [ -n "$SINK_ID" ]; then
		break
	fi
	sleep 1
done

if [ -z "$SINK_ID" ]; then
	echo "apply-audio-output: no sink found for target '$TARGET' in wpctl status" >&2
	exit 1
fi

wpctl set-default "$SINK_ID"
