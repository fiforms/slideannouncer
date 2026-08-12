# updater/

The **local-app** (Tier 2) self-update client — deliberately separate from
the OS-level RAUC OTA tier, since a bad app update can't brick the device
the way a bad OS update can.

**Implemented**: `local_app_updater.py`. Reads the app-update fields
(`app_update_available`, `latest_app_version`, `app_download_url`,
`app_sha256`) that `local-app/backend/heartbeat.py` already caches from its
own periodic heartbeat, so this script makes no network call of its own
until it actually has an update to download. Downloads the release tarball
to `/data/local-app/releases/<version>/`, verifies its checksum, smoke-checks
it (VERSION match + `python3 -m py_compile` on `backend/main.py`), then
atomically swaps the `/data/local-app/current` symlink and restarts the
`slide-announcer-backend`/`slide-announcer-kiosk` services. If the restarted
backend fails its own health check (`GET /api/local/status` over loopback)
within 30s, it auto-reverts the symlink to the previous release and
restarts again — no dual-partition infrastructure required. Keeps the last
`KEEP_RELEASES` (3) releases on disk. A version that fails is not retried
every cycle — only once a newer release shows up.

Runs as its own process (`slide-announcer-local-app-updater.service`, fired
by `slide-announcer-local-app-updater.timer` every 15 minutes), not as an
asyncio task inside the backend the way heartbeat/sync are — the backend
restarting itself mid-update would kill the very process performing the
update. It's fixed OS-image infrastructure (`/opt/slide-announcer/updater/`,
staged by `image-builder/build.sh`/`00-run.sh` the same way
`provisioning/` is), not part of the versioned local-app release it
manages, so a bad app build can never take the update mechanism itself
down with it.

Update *checks* effectively run continuously (bounded only by heartbeat's
5-minute cadence, since that's what refreshes the cached update info this
script reads); the actual download+swap+restart ("apply") is gated to a
configurable idle window (`IDLE_WINDOW_START_HOUR`/`IDLE_WINDOW_END_HOUR`,
default 02:00–05:00 local — see `SLIDE_ANNOUNCER.md`, "Update sequencing/
safety") since restarting the backend causes a brief kiosk hiccup.

**Not yet done**: the kiosk frontend itself doesn't exist yet to observe
that hiccup, and there's no test coverage exercising the download/smoke-check/
revert paths against a real or mocked server response.
