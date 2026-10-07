# Products: one platform, many kiosks

This repo is a kiosk **platform** — the OS image, Wi-Fi/settings shell,
first-run setup, pairing, heartbeat, and two-tier updates — with the
signage behavior layered on as a **product**. A different kiosk (a point of
sale pointed at a remote web app, say) reuses everything but the product.

Product = a directory in four places, all named by the same `<name>`:

| What | Where | Selected by |
|---|---|---|
| Image: settings, packages, units, daemons | `products/<name>/` (repo root) | `PRODUCT=<name> image-builder/build.sh` |
| Backend: routers, background tasks, heartbeat data | `local-app/backend/products/<name>/` | the release's `PRODUCT` file (`KIOSK_PRODUCT` overrides) |
| Frontend: main view, settings pages, overlay | `local-app/frontend/src/products/<name>/` | `KIOSK_PRODUCT` at `npm run build` |
| Server contract | [DEVICE_CONTRACT.md](DEVICE_CONTRACT.md) extensions | `Product.api_base` |

`package.sh`, `dev-deploy.sh` and `build.sh` all take `PRODUCT=<name>`
(default `slideannouncer`) and ship only that product's code.

## Image seams — `products/<name>/`

- `product.env` — shell settings sourced by `kiosk-start.sh`. `KIOSK_URL` is
  what Chromium opens: `http://localhost/kiosk` (the local app) or a remote
  https URL.
- `packages` — extra apt packages, appended to the core list at build time
  (CUPS, for instance). Hotfix bundles can't install packages, so changing
  this means a full image.
- `system/*.service|*.timer` — units installed into `/etc/systemd/system`;
  `system/scripts/*` — installed into `/usr/local/sbin` under the same name.
- `enable` — the units to enable, one per line.

Persistent state must live under `/data` (the root is read-only with an
overlay).

## Backend seam — `product.py`

`products/<name>/__init__.py` exposes `product = Product(...)`:

| field | purpose |
|---|---|
| `api_base` | server path prefix of the device contract |
| `routers` | FastAPI routers mounted on the app |
| `background_tasks` | coroutine functions run beside the heartbeat |
| `status_fields()` | extra keys in `GET /api/local/status` |
| `heartbeat_payload()`, `on_heartbeat_response()` | the contract's extension points |
| `wipe_paths` | state wiped on unpair/revocation |

Core modules must call `product.get()` at the point of use, never import a
product at module level (products import core).

## Frontend seam — `src/product.js`

`src/products/<name>/index.js` default-exports:

| field | purpose |
|---|---|
| `mainView` | component at `/kiosk` |
| `overlay` | component drawn over every route (Menu key) |
| `settingsRoutes` | extra children of `/settings` |
| `railCategories(t)` | extra settings rail entries |
| `advancedSection` | component at the bottom of Settings > Advanced |
| `loadSettings()` | refresh product state when Settings opens |
| `settingsPin()` | the Settings PIN, or `null` |

Product code imports core through the `@core/` alias (`@core/api.js`,
`@core/components/…`); the product's own `api.js` extends the core `api`.

## A remote-portal product (what a POS needs)

Set `KIOSK_URL` to the portal, leave `mainView` unused, and the device boots
straight to it; the setup wizard, Wi-Fi and Settings screens still work at
`http://localhost/…`. **Not built yet:** the escape hatch back to Settings (a
labwc keybinding plus a CDP supervisor that also catches load failures —
generalising `revelation-peer-daemon.py`'s CDP use) and a Chromium URL
allow-list. That's the next phase.

## Still signage-flavoured (known leftovers)

- Names: `slide-announcer-*` units, `/opt/slide-announcer`, the
  `slideannouncer` user, `slideannouncer.yaml`, and the RAUC `compatible`
  string are shared by every product. A second product needs its own
  `compatible` so images can't cross-install.
- `locales/en.json` and `es.json` still hold the signage strings (menu,
  SRT, Revelation); products can't yet contribute their own messages.
- The setup wizard's steps are fixed (Welcome → Network → Name → Pairing →
  Done); the pairing step assumes the contract above.
- `firstboot.py` keeps its own copy of the pairing wipe list (it runs
  standalone, as root), including signage paths.
- Core Python modules still carry signage-flavoured comments and
  docstrings, and `README.md` files describe the original layout.
