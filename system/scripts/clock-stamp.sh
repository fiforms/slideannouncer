#!/bin/sh
# Poor man's RTC for a Pi with no battery-backed clock.
#
#   clock-stamp.sh restore  — at boot: if /data's saved stamp is later than the
#                             current clock, set the clock to it.
#   clock-stamp.sh save     — periodically and at clean shutdown: record the
#                             current time to /data.
#
# systemd-timesyncd's own stamp lives in /var, which is a tmpfs overlay here
# (see 00-run.sh), so it is lost every boot; without this the offline clock
# restarts at the image's build date. The stamp only ever moves forward, so a
# bad offline clock (or a save during early shutdown) can never overwrite a
# better one. After a power cut the clock resumes at the last save, i.e. up to
# an hour (the timer period) behind the moment power was lost, plus however long
# the device was off. NTP corrects it as soon as the network is up.
set -eu

STAMP=/data/clock-stamp
now=$(date -u +%s)
saved=0
if [ -r "$STAMP" ]; then
	saved=$(cat "$STAMP" 2>/dev/null || echo 0)
fi
case "$saved" in
'' | *[!0-9]*) saved=0 ;;
esac

case "${1:-}" in
restore)
	if [ "$saved" -gt "$now" ]; then
		date -u -s "@${saved}" >/dev/null
		echo "clock restored to $(date -u -d "@${saved}" '+%Y-%m-%d %H:%M:%S') UTC (was $(date -u -d "@${now}" '+%Y-%m-%d %H:%M:%S'))"
	else
		echo "clock already at or past saved stamp; leaving as is"
	fi
	;;
save)
	[ -d /data ] && mountpoint -q /data || { echo "/data not mounted; not saving" >&2; exit 0; }
	[ "$now" -gt "$saved" ] || exit 0
	# Atomic: a power cut mid-write must leave the previous stamp intact.
	echo "$now" >"${STAMP}.tmp"
	sync "${STAMP}.tmp"
	mv -f "${STAMP}.tmp" "$STAMP"
	;;
*)
	echo "usage: $0 restore|save" >&2
	exit 2
	;;
esac
