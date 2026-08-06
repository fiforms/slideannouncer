# provisioning/

First-boot setup scripts — the state machine that gets a freshly-flashed
device from "no network" to "paired and showing slides," via three possible
paths, tried in order.

Planned contents (not yet implemented):
- **Boot-partition config check**: on boot, look for
  `/boot/firmware/slideannouncer.yaml` — a plain-text YAML file at the root
  of the Pi's existing FAT32 boot partition, human-editable by inserting the
  card into any Mac/PC (same mechanism Raspberry Pi OS itself uses for
  headless WiFi setup). Holds `wifi.ssid`/`wifi.password` and the declared
  `device_uuid`/`device_uuid_check` pair (see "Device identity" below). If
  WiFi credentials are present here, connect directly — no AP-mode, no
  keyboard needed. This is the true zero-touch path: a card can be
  pre-provisioned before a device is ever powered on.
- **HID detection** (if no usable boot-partition config): check for a usable
  keyboard + relative-pointer input device (e.g. via `libinput`/`evtest`
  introspection of `/dev/input/event*`, not a hardcoded device path) — an
  attached RF remote (keyboard/mouse combo) counts.
  - **If present**: skip AP-mode entirely. Point the kiosk Chromium straight
    at `local-app/frontend/setup` on the device's own display; the admin
    uses the remote to pick a WiFi network and type the password directly
    into the on-screen form, then the numeric pairing code. No second
    device or network is ever involved, so there's no captive-portal
    problem to solve on this path.
- **Fallback path (neither of the above)**: bring up NetworkManager's
  built-in hotspot mode (`nmcli device wifi hotspot`) with a fixed SSID
  rather than a separate hostapd/dnsmasq stack; admin connects with a phone
  and browses to a printed IP. True captive-portal auto-popup is deferred
  for now (see `SLIDE_ANNOUNCER.md`).
- All three paths converge on the same pairing screen and
  `POST /api/slide-announcers/pair` call once WiFi is up.
- Deliberately does **not** auto-fall-back into setup mode on a later WiFi
  drop (that would interrupt a live slideshow on a false-positive blip) —
  only an explicit admin action (the local settings menu's "reset network"
  or "unpair" action) re-enters setup mode.

**Device identity check**: also runs at boot, independent of which setup
path was used. Recomputes `HMAC-SHA256(identity_key, device_uuid +
mac_address)` (secret `identity_key` from `/data/identity.key`, chmod 600 —
never written to the boot partition) and compares against the boot
partition's `device_uuid_check`. Any mismatch — hardware swap, a cloned SD
card, or a hand-edited `device_uuid` without the matching secret — wipes
local state and regenerates a fresh identity + pairing prompt. See
`SLIDE_ANNOUNCER.md`, "Device identity & anti-clone protection" for the full
rationale.
