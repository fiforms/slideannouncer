# The escape hatch: a key combination back to Settings

*Added on branch `server-url-first-run`, alongside [SERVER_URL.md](SERVER_URL.md).
Opt-in per product — nothing changes for a product that doesn't enable the unit.*

## The problem

A product whose main screen is a remote web page (see *A remote-portal product*
in [PRODUCTS.md](PRODUCTS.md)) hands the kiosk's one Chromium tab to a site it
doesn't control. That page can hang, break, or simply not offer a way back, and
the on-device Settings (Wi-Fi, pairing, the product's own pages) can't be
reached from it.

## What it does

`slide-announcer-escape-hatch.service` runs `system/scripts/escape-hatch.py`, which
reads the keyboards directly (evdev, like `volume-key-monitor.py` — it sees keys
even when Chromium is wedged, and doesn't take them away from it). On a trigger it
sends the browser to `http://localhost/settings` through
`slide-announcer-kiosk-navigate` (DevTools Protocol, loopback). The Settings PIN
gate still applies when the page loads.

| Trigger | For |
|---|---|
| `Ctrl`+`Alt`+`Home` | a standard keyboard |
| `Ctrl`+`Alt`+`S` | a keyboard with no Home key (laptops, compact boards) |
| hold the remote's **Home** button for 3 seconds | the remote (`KEY_HOMEPAGE`; Settings > Key Debug shows it as `BrowserHome`) |

Held, not tapped, for the remote: a normal Home press belongs to the page. Edit
`COMBOS` in `escape-hatch.py` to change any of these; keys from different
keyboards combine, either Ctrl/Alt works, and a second trigger within 3 seconds
is ignored.

### If the browser doesn't answer

When the redirect fails (no debug port, no answer, a refusal) the script
**restarts the kiosk unit** instead — `slideannouncer` may already manage
`slide-announcer-*` units through polkit. A fresh browser opens the local app's
main screen, which for a remote-portal product would send it straight back to the
site, so the script first writes `/run/slide-announcer/escape-hatch-hold`. The
product's launcher calls **`POST /api/local/escape-hold/consume`**, which returns
`{"held": true}` once (and never for a flag older than 5 minutes, or after a
reboot) — on `true` the launcher opens `/settings` instead of the site.

## Adopting it in a product

1. List `slide-announcer-escape-hatch.service` in the product's image `enable` file.
   (Core installs the unit and scripts into every image; nothing is enabled unless
   asked for.)
2. If the product's `mainView` redirects to a remote site, have it call
   `/api/local/escape-hold/consume` first and open `/settings` when `held` is true.
   (R Station's `Main.vue` is the example.) A product whose main screen is local
   needs nothing else.

## Files

| File | Role |
|---|---|
| `system/scripts/escape-hatch.py` | the daemon (`Detector` is pure and unit-tested; `escape()` is the action) |
| `system/scripts/kiosk-navigate.py` | `slide-announcer-kiosk-navigate URL`: the one reusable "move the kiosk's tab" CLI |
| `system/slide-announcer-escape-hatch.service` | unit (installed always, enabled by product opt-in) |
| `local-app/backend/escape_hatch.py` + `main.py` | the stay-on-Settings flag and its endpoint |
| `local-app/backend/test_escape_hatch.py` | tests, including an opt-in one against a real Chrome (`KIOSK_CHROME_TEST=1`) |
| `image-builder/stage-slide-announcer/01-system-files/00-run.sh` | installs the two scripts |

## Known duplication, not touched here

The DevTools navigation was already written three times before this:
`local-app/backend/captive_portal.py` (`_cdp_navigate`), `system/scripts/paint-debug.py`,
and a signage product's `slide-announcer-revelation-peer` script. `kiosk-navigate.py`
is meant to be the one place for it; those can move to it (the backend can run it as a
subprocess) in a separate change. Likewise `escape-hatch.py`'s device scan copies
`volume-key-monitor.py`'s and `power-button-monitor.py`'s, which already copy each other.

## Limits

- Confirmed working on a real Pi (2026-10-18). The Chrome redirect itself is also tested
  against a real browser.
- It can't help if Chromium or labwc is gone entirely (the kiosk unit's own
  `Restart=always` handles that), nor if the local nginx/backend is down — Settings
  would then not load.
- A Chromium URL allow-list (to stop the kiosk wandering elsewhere in the first place)
  is still unbuilt.
