# Slide Announcer Audio

Video slides play with sound on the kiosk, routed to whichever output
(HDMI/TV speakers or the Pi's headphone jack) an entity admin selects from
the device's on-screen Settings, with volume/mute driven by the remote's
physical keys. Confirmed working end-to-end on real hardware, shipped as
hotfix `0.3.3` (see `image-builder/hotfixes/0.3.3`).

## Audio stack

The image ships `pipewire`, `pipewire-pulse`, `wireplumber`, and
`alsa-utils` (`image-builder/stage-slide-announcer/01-system-files/00-packages`
— absent from the base image otherwise). `slideannouncer` is in the
`audio` group (`00-run.sh`'s `useradd --groups`, and
`system/slide-announcer-kiosk.service`'s `SupplementaryGroups=` —
separate places, both needed).

There is no systemd `--user` session here (no `loginctl enable-linger`,
no `systemd --user` units) — retrofitting one would be a bigger, riskier
change than this feature needed. Instead `system/scripts/kiosk-start.sh`
backgrounds `pipewire`/`wireplumber`/`pipewire-pulse` itself, under a
manually exported `XDG_RUNTIME_DIR=/run/user/$(id -u)` it also creates,
the same directory Chromium uses. Any other process that wants to talk to
this PipeWire instance — a live Settings-UI change, or the remote's
volume keys — has to set that same `XDG_RUNTIME_DIR` itself, since it has
no session to inherit it from automatically. Both
`apply-audio-output.sh` and `volume-key-monitor.py` do this explicitly
(`export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"` /
`os.environ.setdefault(...)`) — without it, `wpctl` silently talks to no
PipeWire instance at all rather than erroring, since nothing checks its
exit code. This is not a hypothetical: it's exactly what made the first
real-hardware pass of this feature update on-screen state (the Settings
tile, the volume indicator) without ever actually changing what came out
of the speakers.

## Audio output (HDMI / headphone jack)

- `/data/status/audio-output` holds `hdmi` or `headphones` (default
  `hdmi`). Deliberately **not** in `pairing.py`'s `WIPE_PATHS` — this
  describes how the device is physically wired into the room, not its
  pairing state, so it survives unpair/factory-reset-adjacent flows.
- `system/scripts/apply-audio-output.sh` (installed as
  `/usr/local/sbin/slide-announcer-apply-audio-output`) reads that file,
  greps `wpctl status`'s Audio-section `Sinks:` block, and runs
  `wpctl set-default <id>`. Called by `kiosk-start.sh` at boot and by the
  backend whenever Settings changes the value, so a switch takes effect
  without a kiosk restart.
- Sink matching is by substring, not an assumed literal name: real
  hardware (Pi 4, vc4-kms-v3d) shows both outputs as "Built-in Audio",
  distinguished only by a literal `(HDMI)` suffix on the HDMI one
  (`64. Built-in Audio Stereo` / `65. Built-in Audio Digital Stereo
  (HDMI)`). So `"hdmi"` matches that substring, and `"headphones"` matches
  "whichever Built-in Audio sink is NOT the HDMI one" rather than betting
  on a specific name — more likely to hold up across hardware/driver
  revisions than either literal string would.
- Backend: `pairing.py`'s `AUDIO_OUTPUT_FILE`/`read_audio_output()`/
  `write_audio_output()`; `system_control.py`'s `apply_audio_output()`;
  `main.py`'s `GET`/`POST /api/local/audio-output`.
- Frontend: `api.js`'s `audioOutputStatus()`/`setAudioOutput()`, and an
  "Audio Output" section on Settings → System with HDMI/Headphone Jack
  tile buttons (`en`/`es` translated).

## Volume / mute

Shaped like `power-button-monitor.py`/`display-power.py` rather than a
Settings-UI slider — the source of truth is the remote's physical
volume/mute keys, not a web control, since those need to work regardless
of what's on screen (kiosk vs. Settings vs. asleep) and the browser can't
call `wpctl` directly anyway.

- `system/scripts/volume-key-monitor.py` (installed as
  `/usr/local/sbin/slide-announcer-volume-key-monitor`,
  run by its own unit `slide-announcer-volume-key.service`, independent of
  the kiosk): capability-based evdev listener (same discovery pattern as
  `power-button-monitor.py`) for `KEY_VOLUMEUP`/`KEY_VOLUMEDOWN`/
  `KEY_MUTE` — confirmed via Settings → Key Debug as `AudioVolumeUp`,
  `AudioVolumeDown`, `AudioVolumeMute` (Chromium's own legacy DOM keyCodes
  173-175, a different numbering scheme than evdev's 113-115, but the same
  keys). Adjusts in 5% steps (0-100, clamped), honors autorepeat while a
  key is held, auto-unmutes on a volume press (matches normal TV-remote
  behavior), calls `wpctl set-volume`/`set-mute @DEFAULT_AUDIO_SINK@`
  directly, and persists to `/data/status/audio-volume` (plain integer)
  and `/data/status/audio-muted` (`true`/`false`) — same plain-text-file
  pattern as `AUDIO_OUTPUT_FILE`, also excluded from `WIPE_PATHS` for the
  same reason.
- `apply-audio-output.sh` re-applies the persisted volume/mute right
  after selecting the sink, so a boot or an output switch doesn't drop
  back to PipeWire's own ~40% default.
- Backend: `pairing.py`'s `read_audio_volume()`/`read_audio_muted()`
  (read-only — the monitor script above is the only writer); `main.py`'s
  `GET /api/local/audio-volume`, deliberately its own tiny endpoint
  separate from the heavier `/api/local/status`.
- Frontend: no Settings-UI control — the remote is authoritative.
  `Slideshow.vue` pops up a bottom-right level/mute indicator for ~1.5s
  whenever the value changes. Polling is event-triggered, not a fixed
  interval: Chromium receives the same raw `AudioVolumeUp`/`Down`/`Mute`
  keydowns `volume-key-monitor.py` reads from evdev (evdev nodes aren't
  exclusively grabbed), so the page listens for those same keys and calls
  `/api/local/audio-volume` once, ~250ms after the last matching keydown
  (re-armed on autorepeat, so a held key settles to one poll after
  release) — long enough for the monitor script's own debounce + `wpctl`
  call to land first.

## Shipping

The OS-level pieces (`apply-audio-output.sh`, `volume-key-monitor.py`,
`slide-announcer-volume-key.service`) reached already-provisioned devices
as `image-builder/hotfixes/0.3.3` rather than a full reimage, and are
folded into `image-builder/stage-slide-announcer/01-system-files/00-run.sh`
so every future build/OTA has them too. The local-app pieces (the
`/api/local/audio-volume` endpoint, the Settings → Audio Output UI, the
on-screen indicator) ship through the app's own updater channel, not the
OS hotfix mechanism.
