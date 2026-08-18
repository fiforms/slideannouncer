# Slide Announcer Audio — TODO

Goal: video slides play with sound on the kiosk, routed to whichever output
(HDMI/TV speakers or the Pi's headphone jack) an entity admin selects from
the device's on-screen Settings, with volume control to follow.

## 1. Audio output (HDMI / headphone jack) — done, confirmed on real hardware

- [x] Added `pipewire`, `pipewire-pulse`, `wireplumber`, `alsa-utils` to
  `image-builder/stage-slide-announcer/01-system-files/00-packages` — the
  image previously had **no audio server installed at all**.
- [x] `slideannouncer` user granted the `audio` group in both places that
  matter: `00-run.sh`'s `useradd --groups` list, and
  `system/slide-announcer-kiosk.service`'s `SupplementaryGroups=` (these
  are separate and both needed — confirmed by reading the unit file).
- [x] `system/scripts/kiosk-start.sh` backgrounds `pipewire`, `wireplumber`,
  `pipewire-pulse` under the same manually-built `XDG_RUNTIME_DIR` Chromium
  uses, then calls the sink-selector script below — there's no
  logind/systemd user session on this image (see that script's own
  docstring), so the normal way PipeWire/WirePlumber autostart doesn't
  apply here; this is a deliberate, simpler alternative.
- [x] New `system/scripts/apply-audio-output.sh`
  (installed as `/usr/local/sbin/slide-announcer-apply-audio-output`):
  reads `/data/status/audio-output` (`hdmi`/`headphones`, default `hdmi`),
  greps `wpctl status`'s Audio-section `Sinks:` block, and runs
  `wpctl set-default <id>`. Callable standalone (not just at boot) so a
  live Settings change applies immediately.
- [x] Backend: `pairing.py` gained `AUDIO_OUTPUT_FILE`/`read_audio_output()`/
  `write_audio_output()` (same pattern as `LANGUAGE_FILE`), deliberately
  **excluded** from `WIPE_PATHS` — a hardware output preference describes
  how the device is wired into the room, not its pairing state, so it
  shouldn't reset on unpair. `system_control.py` gained
  `apply_audio_output()`; `main.py` gained
  `GET`/`POST /api/local/audio-output`.
- [x] Frontend: `api.js`'s `audioOutputStatus()`/`setAudioOutput()`, and a
  new "Audio Output" section on Settings → System with HDMI/Headphone Jack
  tile buttons (`en`/`es` translated).
- [x] **Confirmed working on real hardware** (2026-08-17): installed the
  packages manually over SSH, ran `wpctl status`, discovered neither sink
  is actually named "Headphones" — both show as "Built-in Audio", the HDMI
  one distinguished only by a literal `(HDMI)` suffix
  (`64. Built-in Audio Stereo` / `65. Built-in Audio Digital Stereo (HDMI)`)
  — adjusted the matching logic accordingly (see script's own comment).
  Rebuilt `kiosk-start.sh`'s service, confirmed video plays with sound over
  HDMI. Headphone-jack output not physically tested (no speaker/cable handy
  at the time) but the same `wpctl set-default` mechanism applies —
  reasonably high confidence, not yet hardware-confirmed.
- [ ] Still open: physically confirm headphone-jack output once there's a
  speaker to plug in.
- [ ] Still open: build the next full OS image
  (`image-builder/build.sh`) with these changes and roll it out.

## 2. Volume control — not started

Both sinks came up at `[vol: 0.40]` (40%) by default on the test hardware —
noticeably quieter than expected, and nothing in this project set that; it
appears to be PipeWire/WirePlumber's own default. Confirmed `wpctl
set-volume <id> <value>` (e.g. `1.0` for 100%, or `@DEFAULT_AUDIO_SINK@` to
target whichever sink is currently active without knowing its numeric id)
works as the same unprivileged `slideannouncer` user, no extra privilege
needed — same reasoning as `set-default`.

Planned shape, mirroring audio-output exactly:
- [ ] `pairing.py`: `AUDIO_VOLUME_FILE = Path("/data/status/audio-volume")`,
  `read_audio_volume()`/`write_audio_volume()` (a percentage, e.g. `0`-`150`,
  default TBD — probably `100`).
- [ ] Extend `apply-audio-output.sh` (or a sibling script) to also run
  `wpctl set-volume @DEFAULT_AUDIO_SINK@ <value>%` right after selecting
  the sink, so one call re-applies both output and volume together.
- [ ] `system_control.py`/`main.py`: fold into the same
  `apply_audio_output()` flow, or add a parallel
  `GET`/`POST /api/local/audio-volume` — decide when building this; folding
  into one call is probably simpler since output and volume are always
  applied together anyway.
- [ ] Frontend: a volume slider (`<input type="range">`) on Settings →
  System next to the HDMI/Headphone Jack tiles — first control of this kind
  in this settings UI (everything else so far is tile-buttons or plain
  inputs), so it'll set the pattern rather than follow one.

## Decisions

- **No systemd user session for PipeWire.** Retrofitting one (`loginctl
  enable-linger`, systemd `--user` units) would be a bigger, riskier change
  than this feature needs — `kiosk-start.sh` already manually bootstraps
  its own `XDG_RUNTIME_DIR` with no logind involved, so PipeWire/WirePlumber
  join that same pattern as plain backgrounded processes instead.
- **Match by substring, not by an assumed literal sink name.** The original
  plan assumed a sink literally named "Headphones" would exist (a common
  naming convention on some Raspberry Pi OS audio stacks) — real hardware
  showed otherwise. The matching script now treats "headphones" as "the
  Built-in Audio sink that isn't the HDMI one" rather than betting on a
  specific name, since that's more likely to hold up across hardware/driver
  revisions than either literal string would.
- **Audio-output preference survives unpair/factory-reset-adjacent flows**
  (not in `WIPE_PATHS`) — it's a property of how the device is physically
  wired into the room (TV vs. a PA system on the jack), independent of
  which entity it's paired to.
