# The kiosk guard: a way back on screen, and a rescue for lost pages

*Complements [ESCAPE_HATCH.md](ESCAPE_HATCH.md) (a key combination). Opt-in per
product — list `slide-announcer-kiosk-guard.service` in the image `enable` file.*

## The problem

The kiosk's one Chromium tab is handed to pages we don't control — a remote
portal, CUPS's admin pages. They may offer no way back, may be on a dead network,
or may break. The escape hatch needs a keyboard and someone who knows the keys.

## What it does

`slide-announcer-kiosk-guard.service` runs `system/scripts/kiosk-guard.py`, which
holds one DevTools connection (loopback) to the kiosk's Chromium and, every 2 s:

1. **Floating button.** On every page that isn't the local app
   (`http://localhost/…`; CUPS on `:631` counts as remote) it injects a small
   **⌂ Settings** pill (bottom right, Shadow DOM so page CSS can't touch it,
   top frame only) via `Page.addScriptToEvaluateOnNewDocument`, so it survives
   navigations. Clicking it opens `http://localhost/settings`.
2. **Network state.** It checks the page's own server is reachable (TCP connect
   to the page's host:port, every 5 s, two failures in a row) and tells the
   button, which turns amber: **No connection — Settings**. The browser's own
   `offline` event does the same. *Telling the user is all it does.*
3. **Rescue — only for a page that is already lost.** A `chrome-error://` page,
   or a page that stops answering the protocol (3 checks in a row), for 15 s
   straight, is navigated to Settings. At most once a minute, so a down local app
   can't loop it. If the navigation itself fails it restarts the kiosk unit and
   leaves the escape hatch's stay-on-Settings flag.

## What it deliberately does not do

**It never navigates away from a page that is working, however long the network
is down.** A page may hold unsent data, or a service worker may be queueing work
until the network returns; a forced navigation would lose it. The amber button
hands the decision to the person. The only automatic navigation is for a page
that's already an error page or not responding at all — nothing left to lose.

Known limits: a page that is running but useless (a blank white page, an app
spinner that never ends) is not "lost" by these rules, and the button is the way
out. A page with a modal `alert()` open doesn't answer the protocol and will be
rescued after 15 s. The button needs a pointer or touch; keyboard users have the
escape hatch.

## Files

| File | Role |
|---|---|
| `system/scripts/kiosk-guard.py` | the daemon (`Guard`, `Reachability` are pure; `cycle()` is one pass; `BUTTON_SCRIPT` is the injected JS) |
| `system/slide-announcer-kiosk-guard.service` | unit (installed always, enabled by product opt-in) |
| `local-app/backend/test_kiosk_guard.py` | tests, incl. an opt-in one against a real Chrome (`KIOSK_CHROME_TEST=1`) — passes |

Not yet tried on a real Pi.
