# Slide Announcer Screens

An entity admin picks the kiosk's display resolution (4K or 1080p) from
the device's on-screen Settings → Screens page. If a second HDMI output is
connected, it's automatically mirrored at the same mode — there's no
separate control for that yet. **Not yet confirmed on real hardware** —
this whole feature was built without a Pi in hand to test against (unlike
`AUDIO_IMPLEMENTATION.md`'s audio-output feature, which shipped a similar
first pass and then got fixed against real hardware bugs). Read this
document's "Untested assumptions" section before relying on it.

## Resolution

- `/data/status/screen-resolution` holds `4k` or `1080p` (default
  `1080p` — the safer/more broadly-compatible mode across unknown TV/HDMI-
  cable hardware). Deliberately **not** in `pairing.py`'s `WIPE_PATHS` —
  this describes how the device is physically wired into the room, not its
  pairing state, same reasoning as `AUDIO_OUTPUT_FILE`.
- `system/scripts/apply-screen-resolution.sh` (installed as
  `/usr/local/sbin/slide-announcer-apply-screen-resolution`) reads that
  file, lists `wlr-randr`'s connected outputs, and sets each to the target
  mode: `3840x2160@30Hz` for `4k`, `1920x1080@60Hz` for `1080p`. 4K is
  pinned to 30Hz rather than 60Hz as a safer default across HDMI cable/TV
  variance for a kiosk signal — worth revisiting once real 4K hardware is
  in front of someone.
- Called by `kiosk-start.sh`'s labwc session command (`-s`) right after the
  compositor starts and before Chromium launches, and by the backend
  whenever Settings changes the value, so a switch takes effect without a
  kiosk restart. Unlike `apply-audio-output.sh` (which only needs
  PipeWire, so it runs *before* `exec labwc`), this script needs a live
  compositor to talk to — see `kiosk-start.sh`'s comment on why it's
  folded into the `-s` session command instead.
- Backend: `pairing.py`'s `SCREEN_RESOLUTION_FILE`/`read_screen_resolution()`/
  `write_screen_resolution()`; `system_control.py`'s
  `apply_screen_resolution()`; `main.py`'s `GET`/`POST
  /api/local/screen-resolution`.
- Frontend: `api.js`'s `screenResolutionStatus()`/`setScreenResolution()`,
  and a "Screens" entry in the Settings left rail
  (`views/settings/Screens.vue`) with 4K/1080p tile buttons, matching the
  Audio Output page's shape (`en`/`es` translated).

## Multi-monitor: mirror only

The Pi 4 B has two HDMI outputs. `apply-screen-resolution.sh` sets *every*
output `wlr-randr` reports to the same mode at the same position
(`--pos 0,0`) — i.e. mirrors — rather than extending the desktop across
both. This is the only behavior today; there's no Settings UI toggle for
it. A genuinely independent slideshow per output is a much bigger feature
(likely two separate Chromium+kiosk-service instances, one per output) and
is written up as a documented-but-unbuilt idea in the main repo's
`SLIDE_ANNOUNCER.md`, "Future idea: independent slideshow per monitor" —
not started, and unverified whether the hardware/compositor stack even
supports it cleanly.

## Untested assumptions

None of this has been run against the actual Pi 4 B hardware yet. Specific
things to confirm on a real device, roughly in order:
1. `wlr-randr` is added to `00-packages` but the image hasn't been rebuilt
   and flashed with it — confirm it's actually present and runnable as the
   `slideannouncer` user post-boot.
2. Single-HDMI first: does `wlr-randr` list the expected output name
   (assumed something like `HDMI-A-1`, the typical vc4-kms-v3d/wlroots
   naming — same kind of assumption `apply-audio-output.sh` had to confirm
   for `wpctl` sink names), and does setting `1920x1080@60Hz`/
   `3840x2160@30Hz` actually change what's on screen?
3. `kiosk-start.sh`'s `-s` session-command change (folding
   `apply-screen-resolution.sh` in ahead of the `exec chromium…` call) —
   confirm labwc actually runs it as a shell command with `;` sequencing
   the way it's written, and that a `wlr-randr` failure (`|| true`) doesn't
   block Chromium from starting.
4. Then dual-HDMI: connect a second monitor, confirm both outputs actually
   report as connected to `wlr-randr` and both mirror correctly — this is
   the part the user has explicitly never tried at all, on any software
   stack, before this feature existed.
5. `XDG_RUNTIME_DIR`/`WAYLAND_DISPLAY` guesses in
   `apply-screen-resolution.sh` for the backend-triggered call path (no
   session to inherit them from, same class of bug `apply-audio-output.sh`
   hit for PipeWire's `XDG_RUNTIME_DIR` the first time this pattern was
   used) — confirm a Settings-UI resolution change while the kiosk is
   already running actually re-applies live, not just at next boot.

## Shipping

Like the audio-output feature, the OS-level piece (`wlr-randr` package,
`apply-screen-resolution.sh`, the `kiosk-start.sh` change) needs a new
image build/OTA to reach already-provisioned devices — a hotfix bundle is
the lighter path if the only change needed after hardware testing is
within these files, per `image-builder/hotfixes/README.md`. The local-app
pieces (the `/api/local/screen-resolution` endpoint, the Settings →
Screens UI) ship through the app's own updater channel, not the OS hotfix
mechanism.
