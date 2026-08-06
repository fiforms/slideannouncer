# provisioning/

First-boot setup scripts — the state machine that gets a freshly-flashed
device from "no network" to "paired and showing slides," via two possible
input paths.

Planned contents (not yet implemented):
- **HID detection**: on boot, check for a usable keyboard + relative-pointer
  input device (e.g. via `libinput`/`evtest` introspection of
  `/dev/input/event*`, not a hardcoded device path) — an attached RF remote
  (keyboard/mouse combo) counts.
- **Primary path (HID present)**: skip AP-mode entirely. Point the kiosk
  Chromium straight at `local-app/frontend/setup` on the device's own
  display; the admin uses the remote to pick a WiFi network and type the
  password directly into the on-screen form, then the numeric pairing code.
  No second device or network is ever involved, so there's no
  captive-portal problem to solve on this path.
- **Fallback path (no HID detected)**: bring up NetworkManager's built-in
  hotspot mode (`nmcli device wifi hotspot`) with a fixed SSID rather than a
  separate hostapd/dnsmasq stack; admin connects with a phone and browses to
  a printed IP. True captive-portal auto-popup is deferred for now (see
  `SLIDE_ANNOUNCER.md`) — this fallback still assumes someone is physically
  on-site with a phone, not true zero-touch provisioning (that's a separate,
  not-yet-designed follow-up).
- Deliberately does **not** auto-fall-back into setup mode on a later WiFi
  drop (that would interrupt a live slideshow on a false-positive blip) —
  only an explicit admin action (the local settings menu's "reset network"
  or "unpair" action) re-enters setup mode.

See `SLIDE_ANNOUNCER.md`, "First-boot / WiFi setup flow" for the full state
machine, and "Device identity & anti-clone protection" for how unpairing
ties into the same wipe-and-reboot path.
