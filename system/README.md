# system/

systemd units, nginx config, and polkit rules that wire together the
compositor, kiosk Chromium, and `local-app/` services on the device.

## Contents

- `slide-announcer-factory-reset-check.service` + `scripts/factory-reset-check.sh`
  — runs before `/data` is ever mounted (`DefaultDependencies=no`,
  `Before=data.mount`, same shape as `overlay-var-dirs.service` below).
  If `/boot/firmware/FACTORY_RESET` exists, reformats `/data` and removes
  the flag; otherwise a no-op (`ConditionPathExists`). No second reboot
  needed — boot just continues into the now-empty `/data`, which every
  other boot-time piece here (identity, local-app seeding, WiFi via the
  `/etc` overlay) already knows how to handle correctly, because that's
  exactly what a brand-new SD card looks like too. Triggered from the
  Settings > System UI (`slide-announcer-factory-reset-trigger.service`,
  below) or by hand (mount the boot partition on any PC/Mac and create the
  file, same as `slideannouncer.yaml`).
- `slide-announcer-data-dirs.service` — recreates `/data/overlay/etc/`'s
  upper/work dirs before `/etc`'s overlay mount, every boot (cheap,
  idempotent) — needed because a factory reset wipes them at runtime,
  unlike `image-builder/repartition.sh`'s one-time build-time seed.
- `slide-announcer-home-dirs.service` + `scripts/home-dirs.sh` — creates
  and seeds (from `/etc/skel`, once) `/data/home/slideadmin` before
  `/home/slideadmin` bind-mounts onto it (`00-run.sh`'s fstab entry) —
  same reasoning as `slide-announcer-data-dirs.service` above, just for the
  `slideadmin` console/SSH account's home instead of `/etc`'s overlay:
  plain root is otherwise `ro`, so without this, bash history and anything
  else written under that home dir would reset on every reboot/OTA.
- `slide-announcer-factory-reset-trigger.service` — on-demand (never
  enabled at boot) unit the backend starts via the polkit rule below: sets
  `/boot/firmware/FACTORY_RESET` and reboots.
- `slide-announcer-data-resize.service` + `scripts/data-resize.sh` — grows
  the `/data` partition (partition 4) to fill the real SD card on first
  boot via `growpart`/`resize2fs`, since it's created as a small
  placeholder inside the built `.img` (see `../image-builder/repartition.sh`
  — "rest of the card" is only known once it's on real hardware). Runs
  before everything else; masks the stock Raspberry Pi OS root-resize unit,
  since `rootA` is a fixed size by design (the future RAUC A/B slot pair).
- `slide-announcer-firstboot.service` — runs
  `provisioning/firstboot.py` (SSH host key/machine-id regen, device
  identity check, setup-mode detection, and — every boot, not just the
  first — syncing `slideannouncer.yaml`'s `ssh_authorized_keys` field to
  `slideadmin`'s `~/.ssh/authorized_keys`, which only persists at all
  because of the home bind mount above). Every boot also runs
  `set_hostname()`: a stable per-device hostname
  (`slideannouncer-######`, a 6-digit number derived from `device_uuid`,
  or a `hostname` override from `slideannouncer.yaml`), via
  `hostnamectl set-hostname` plus a matching `/etc/hosts` patch — needed
  since every device otherwise boots with the identical hostname baked in
  at image-build time. mDNS (`avahi-daemon`, already
  `publish-workstation=yes`) then makes `<hostname>.local` resolvable,
  which is how the SRT sink daemon (below) is addressed from Windows/
  macOS.
- `slide-announcer-local-app-seed.service` + `scripts/local-app-seed.py` —
  runs every boot, before the backend/kiosk services: extracts the
  local-app release tarball baked into this image at
  `/opt/slide-announcer/local-app-release/` onto `/data/local-app/releases/`
  and swaps the `/data/local-app/current` symlink, but only if `/data` has
  none installed yet or an older one — never downgrades. See
  `../local-app/README.md`'s "Installation on the device" for the full
  design (this is what lets local-app live entirely on `/data`, on the same
  atomic symlink-swap layout the future `updater/` will maintain, instead
  of being baked straight into the read-only rootfs).
- `slide-announcer-backend.service` — the local backend
  (`local-app/backend/main.py`, FastAPI, running from
  `/data/local-app/current/backend` under the fixed
  `/opt/slide-announcer/venv`) — local status plus the WiFi/network
  settings API; pairing and slide sync from `SLIDE_ANNOUNCER.md`'s Tier 2
  design are still not implemented.
- `slide-announcer-kiosk.service` + `scripts/kiosk-start.sh` — starts
  `labwc` with Chromium autostarted in kiosk mode against the local
  nginx-served app. Runs as the dedicated `slideannouncer` user via
  `seatd`, no display manager/logind session involved.
- `chromium-policies/slide-announcer.json` — Chromium enterprise policy
  (installed to `/etc/chromium/policies/managed/`): disables the password
  manager and autofill, so entering the WiFi password in Settings >
  Network doesn't trigger a "Save password?" bubble — a kiosk has no user
  account concept to save it for (policy, not a command-line flag: modern
  Chromium removed the flags that used to do this); `DeveloperToolsAvailability: 2`
  as defense-in-depth alongside `--kiosk`'s own DevTools block. Browser-
  native function-key shortcuts (F1 Help, F3 find-in-page, F7 caret
  browsing) have no Chromium policy to disable the key itself on Linux —
  longstanding open upstream request,
  https://bugs.chromium.org/p/chromium/issues/detail?id=552451. Deliberately
  *not* using `URLBlocklist`/`URLAllowlist` to contain F1's "opens a fully
  browsable window" risk, since planned features may need real internet/
  network access from the kiosk frontend itself; those keys (plus Alt-F4,
  which closes the Chromium window outright) are instead swallowed one
  layer down, in the compositor — see `labwc/rc.xml`, below.
- `labwc/rc.xml` — installed to
  `/var/lib/slide-announcer/.config/labwc/rc.xml` (the `slideannouncer`
  user's home) by `00-run.sh`. Binds F1-F12 and Alt-F4 to a real,
  visible-effect-free action (`Execute command="true"`) so the compositor
  consumes those keys before Chromium's own accelerator table ever sees
  them — this is how F1/F3/F7/F12/Alt-F4 actually get blocked, since none
  of them have a Chromium policy on Linux (see `chromium-policies/`,
  above). Deliberately omits `<default/>`, so no other labwc keybindings
  (Alt-Tab, etc.) get loaded either — this kiosk has one fullscreen
  surface and nothing to switch between.
- `scripts/display-power.py` (installed as
  `/usr/local/sbin/slide-announcer-display-power`) — the actual sleep/wake
  mechanism: `sleep` stops `slide-announcer-kiosk.service` then
  `vcgencmd display_power 0`; `wake` is the reverse; `toggle`/`status`
  read/flip a marker file at `/run/slide-announcer/kiosk-sleeping` (tmpfs —
  always starts awake on boot). `takeover` stops the kiosk and ensures
  HDMI is on *without* touching that marker — used by the SRT sink daemon
  (below) to take the display for external video while leaving whatever
  sleep state the device was already in untouched, so a later unconditional
  `wake`/`sleep` call (based on a `status` snapshot from before `takeover`)
  correctly restores it. Deliberately a standalone CLI, not logic embedded
  in whatever happens to trigger it, so every trigger (today, the remote's
  power key, and the SRT sink daemon; later, a schedule or a web UI menu
  button) shares one mechanism instead of each re-implementing it. Callable
  directly, without sudo, as root, `slideannouncer`, or `slideadmin` — the
  `systemctl`/`vcgencmd` calls it makes are already permitted for all
  three (see the script's own docstring for exactly why: the existing
  `slide-announcer-*.service` polkit grant, and both accounts' persistent
  `video` group membership).
- `slide-announcer-power-button-dirs.service` — creates
  `/run/slide-announcer` (root:`slideannouncer` group, mode 0775) before
  anything needs to write the marker file above — group, not owner, is
  what lets `slideannouncer`/`slideadmin` create/remove it themselves.
- `slide-announcer-power-button.service` + `scripts/power-button-monitor.py`
  — runs as the unprivileged `slideannouncer` user (reading
  `/dev/input/event*` only needs the `input` group, which that account
  already has), independent of `slide-announcer-kiosk.service` (it has to
  keep running while the kiosk is stopped). Scans `/dev/input/event*` for
  any device supporting `KEY_POWER` (the remote's power button —
  capability-based, not pinned to a vendor/product ID, since this device
  only ever has the one remote) and calls
  `slide-announcer-display-power toggle` on each press. Added because
  logind's default power-key handling (suspend) is what the remote used to
  trigger, and the Pi can't reliably resume from that suspend — see
  `logind/`, below, which is what actually stops logind from racing this
  daemon for the same keypress.
- `logind/slide-announcer-power-button.conf` — installed to
  `/etc/systemd/logind.conf.d/`: `HandlePowerKey=ignore` +
  `HandlePowerKeyLongPress=ignore`, so logind's own suspend-on-power-key
  behavior gets out of the way of `slide-announcer-power-button.service`
  above entirely. Only takes effect once `systemd-logind`
  restarts/reboots.
- `slide-announcer-srt-sink.service` + `scripts/srt-sink-monitor.py`
  (installed as `/usr/local/sbin/slide-announcer-srt-sink-monitor`) —
  on-demand SRT video-sink: polls UDP port 7002 (a plain bound socket —
  nothing normally listens there, so this daemon has to hold one itself
  to see any traffic at all), and on any datagram, closes that socket and
  runs a one-shot mpv probe (`--vo=null --ao=null`) against the
  configured passphrase from Settings > SRT Sink
  (`local-app/backend/srt_sink.py`, `/data/status/srt-sink.json`). SRT's
  own HSv5 handshake is what actually rejects a wrong passphrase — no
  separate check needed here. Only on a successful probe does it call
  `slide-announcer-display-power takeover` and launch a real mpv (direct
  DRM/KMS video, direct ALSA audio — neither PipeWire nor labwc is running
  by then, kiosk-start.sh only starts those inside the kiosk unit's own
  cgroup), blocking until the stream ends, then restoring whichever of
  `wake`/`sleep` matches the state captured before `takeover` ran. Runs as
  the unprivileged `slideannouncer` user (same `video`/`render`/`audio`
  supplementary groups as the kiosk unit), independent of
  `slide-announcer-kiosk.service` — always running; the config file's
  `enabled` flag (checked every poll) is what actually gates behavior, so
  toggling it in Settings takes effect within one ~2s poll interval, no
  unit restart needed.
- `nginx-slide-announcer.conf` — serves `/data/local-app/current/frontend`
  (the Vue SPA, following the `current` symlink at request time — see
  `../local-app/README.md`) and reverse-proxies `/api/*` to the backend on
  loopback only.
- `polkit/50-networkmanager-slide-announcer.rules` — grants the
  `slideannouncer` service user NetworkManager D-Bus control, used by the
  backend's `network.py` (`nmcli` scan/connect/status calls backing the
  Settings > Network menu — see `../local-app/README.md`).
- `polkit/50-slide-announcer-system.rules` — grants `slideannouncer`
  exactly two more things: `systemctl reboot` (via logind's own reboot
  action), and starting units named `slide-announcer-*.service` (never
  arbitrary system units). Backs the Settings > System menu's "Restart
  Device," "Check for Update," and "Factory Reset" — see
  `../local-app/README.md`'s "Privileged operations from the web UI" for
  the full design.
- `slide-announcer-update-check.service` + `scripts/update-check.py` — an
  on-demand (never enabled at boot) oneshot unit the backend starts via the
  polkit rule above. Runs as root, calls `slide-announcer-update check`,
  and writes the result to `/data/status/update-check.json` for the
  unprivileged backend to read back.
- `read-only-root/overlay-var.conf` — tmpfiles.d rule creating
  `/run/overlay-var`'s upper/work dirs fresh every boot, backing `/var`'s
  writable overlay (see below).
- `read-only-root/journald-volatile.conf` — journald drop-in forcing
  `Storage=volatile`, since `/var/log/journal` would never persist across a
  reboot anyway once `/var` is overlay-tmpfs.
- `read-only-root/rpi-swap-data.conf` — `rpi-swap` (this image's actual swap
  mechanism, a zram + file-backed-overflow hybrid — not `dphys-swapfile`,
  which isn't installed here) drop-in at `/etc/rpi/swap.conf.d/`, repointing
  the file half of that hybrid off its default `/var/swap` (RAM, since
  `/var`'s upper is tmpfs) to a fixed 2048MB file on `/data` (real disk,
  cheap even on the smallest supported card — see the drop-in's own
  comment). No
  manual ordering against `data.mount` needed — `rpi-swap-generator`
  derives each unit's `RequiresMountsFor=` from this file's `Path=` fresh
  on every boot/`daemon-reload`.
- `ssh/ssh-gate.conf` + `scripts/ssh-gate.py` — `ssh.service.d` drop-in
  (`ExecStartPre=`) that refuses to let sshd start at all unless
  `slideannouncer.yaml` says `ssh_enabled: true`, checked fresh on every
  start attempt. `ssh.service` is `systemctl enable`d in every image now
  regardless of build flags (`image-builder/config`'s `ENABLE_SSH=1`) —
  this is what makes "is SSH actually reachable" a runtime, per-device,
  yaml-driven decision instead (edit the boot yaml + reboot/`systemctl
  restart ssh` to flip it, no rebuild/reflash), the same way
  `ssh_authorized_keys` already is.
- `ssh/pubkey-only.conf` — `sshd_config.d` drop-in disabling password
  authentication globally, installed unconditionally in every image —
  there is no build mode (dev or otherwise) that ever ships a device
  capable of password-over-SSH. Key-based login for `slideadmin` is
  entirely driven by `slideannouncer.yaml`'s `ssh_authorized_keys` (see
  `slide-announcer-firstboot.service`, above) — a device with SSH turned on
  (`ssh_enabled: true`) but no key configured simply has no way in at all.
- `read-only-root/cloud-init-etc-overlay.conf` — `cloud-init-local.service`
  drop-in (`After=`/`Requires=etc.mount`). Stock `cloud-init-local.service`
  has `DefaultDependencies=no` and no ordering against `/etc`'s overlay
  mount, since on a normal system `/etc` is just part of the root
  filesystem. Here it's `etc.mount`, only ready after
  `slide-announcer-factory-reset-check.service` -> `data.mount` ->
  `slide-announcer-data-dirs.service` -> `etc.mount`. Without this,
  cloud-init can win the race and write the NoCloud network-config's
  WiFi profile before that chain finishes — usually harmless since the
  chain is normally fast, but a `FACTORY_RESET` boot adds a real
  `mkfs.ext4` ahead of `etc.mount`, which was enough to lose the profile
  and require a second reboot to pick up WiFi.
- `rauc/` — `system.conf` (slot config, including the `kernel.0`/`kernel.1`
  custom slots for boot-partition content), `rpi-tryboot-backend.sh`
  (get/set-primary — stages a tryboot attempt) and `rpi-tryboot-commit.sh`
  (runs at boot, commits a successful tryboot attempt). Confirmed working
  end-to-end on real hardware (2026-08-11) — see
  `../image-builder/README.md`'s note and each script's own header. The
  forced-bad-health → rollback path is still unverified (today's health
  check is a placeholder).
- `slide-announcer-rauc-dirs.service` — creates `/data/rauc` for RAUC's
  persistent statusfile before RAUC ever runs. Not a plain tmpfiles.d rule:
  `systemd-tmpfiles-setup.service` runs before `/data` mounts, so a bare
  rule there either fails against the still-read-only rootfs or gets
  shadowed once `/data` mounts on top — same class of ordering bug
  `slide-announcer-data-dirs.service` already solves for `/etc`'s overlay
  dirs, fixed the same way here. Found on real hardware: `rauc install`
  failed with "Failed to create file '/data/rauc/central.json.XXXXXX':
  No such file or directory".
- `slide-announcer-tryboot-check.service` — runs `rpi-tryboot-commit.sh`
  every boot; a no-op unless this boot was a RAUC tryboot attempt.
- `scripts/rauc-update.py` (installed as `/usr/local/sbin/slide-announcer-update`)
  — manual CLI for testing the RAUC OTA pipeline: `check` (calls the server
  heartbeat), `install [url-or-path]`, `tryboot` (reboots into the staged
  slot), `status`, `mark-good`. No automatic timer/idle-window gating yet —
  see the script's own module docstring for what it does and doesn't cover.

## Read-only rootfs

`rootA`/`rootB` are mounted `ro`; `/etc` and `/var` are each a CoW overlay
back over themselves so services can still write to the paths they expect
— `/etc`'s upper layer lives on `/data` (survives reboots: SSH host keys,
`machine-id`, future NetworkManager WiFi profiles), `/var`'s is tmpfs
(logs/runtime state, reset every boot). The fstab/cmdline wiring lives in
`image-builder/stage-slide-announcer/01-system-files/00-run.sh`;
`/data`'s filesystem itself is created at image-build time
(`image-builder/repartition.sh`), but left otherwise empty — `/etc`'s
upper/work directories on it (which must exist before the overlay can
mount at all) are created at boot instead, by
`slide-announcer-data-dirs.service` (above), on every single boot
including the device's very first one. That's what makes a brand-new SD
card and a post-factory-reset `/data` (which the same service also has to
handle — see `system/scripts/factory-reset-check.sh`) go through exactly
the same code path, rather than keeping a separate build-time seed in sync
with it. See `SLIDE_ANNOUNCER.md`, Tier 1, "Read-only rootfs" for the full
rationale.

**Not yet implemented:** the slide sync daemon and real kiosk slideshow
rendering (Tier 2); automatic OS update checks/idle-window gating (Tier 1
still needs `slide-announcer-update` triggered by hand). RAUC's A/B
tryboot switching itself (see `rauc/`, above) is confirmed working
end-to-end on real hardware — install → tryboot → commit has been run on
an actual device (0.1.10 → 0.2.1, 2026-08-13), including a subsequent
power cycle confirming the committed slot stays the default. The
forced-bad-health → rollback path is still unverified (today's health
check is a placeholder — see above).

See the main repo's `SLIDE_ANNOUNCER.md` for how these units interact across
update tiers (e.g. why local-app restarts are gated to an idle window).
