# provisioning/

First-boot and AP-mode WiFi setup scripts — the state machine that gets a
freshly-flashed device from "no network" to "paired and showing slides."

Planned contents (not yet implemented):
- First-boot detection: if no known WiFi connection exists, bring up
  NetworkManager's built-in hotspot mode (`nmcli device wifi hotspot`) with
  a fixed SSID, rather than a separate hostapd/dnsmasq stack.
- Setup-mode → station-mode transition once the admin submits WiFi
  credentials through `local-app/frontend/setup`.
- Deliberately does **not** auto-fall-back into AP mode on a later WiFi
  drop (that would interrupt a live slideshow on a false-positive blip) —
  only an explicit admin action re-enters setup mode.

See `SLIDE_ANNOUNCER.md`, "First-boot / WiFi setup flow" for the full state
machine and the rationale for skipping true captive-portal auto-popup in v1.
