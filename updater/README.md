# updater/

The **local-app** (Tier 2) self-update client — deliberately separate from
the OS-level Mender OTA tier, since a bad app update can't brick the device
the way a bad OS update can.

Planned contents (not yet implemented):
- `local_app_updater.py` — polls `GET /api/slide-announcers/heartbeat` (the
  app-update check is folded into the heartbeat response) or a dedicated
  version endpoint, compares against `/data/local-app/current/VERSION`,
  downloads the release tarball to `/data/local-app/releases/<version>/`,
  runs a smoke check, then atomically swaps the `/data/local-app/current`
  symlink and restarts the `local-app` systemd services. Auto-reverts the
  symlink and restarts again if the new version fails its own health check
  within N seconds — no dual-partition infrastructure required.

Update *checks* run continuously; the actual download+restart is gated to a
configurable idle window (see `SLIDE_ANNOUNCER.md`, "Update sequencing/
safety") since restarting the backend causes a brief kiosk hiccup. Keeps the
last 2-3 releases on disk for instant rollback.
