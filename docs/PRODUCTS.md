# Products: one platform, many kiosks

This repo is a kiosk **platform** — the OS image, Wi-Fi/settings shell,
first-run setup, pairing, heartbeat, and two-tier updates — with the
signage behavior layered on as a **product**. A different kiosk (a point of
sale pointed at a remote web app, say) reuses everything but the product.

Product = one directory, **outside this repo**, named by `PRODUCT_ROOT`:

```
<product>/
  image/      product.env, packages, enable, system/      → the OS image
  backend/    a Python package exposing `product`          → backend/products/<name>/
  frontend/   index.js + the product's Vue code            → src/products/<name>/
```

The name is `PRODUCT` if set, else the directory's basename. Build with:

```bash
PRODUCT_ROOT=/path/to/my-product image-builder/build.sh        # OS image + RAUC bundle
PRODUCT_ROOT=/path/to/my-product local-app/package.sh         # local-app release only
PRODUCT_ROOT=/path/to/my-product local-app/dev-deploy.sh user@host
PRODUCT_ROOT=/path/to/my-product local-app/run-tests.sh       # core + product backend tests
```

The scripts (via `local-app/product-env.sh`) copy this repo's sources and
the product into a throwaway build tree, so this repo — typically a pinned
submodule of the product's own repo — is never modified. `examples/portal/`
is a complete minimal product; copy it to start. The signage product is
`kiosk-products/slideannouncer/` in the AnnouncementSlides repo.

The server side is the product's other half:
[DEVICE_CONTRACT.md](DEVICE_CONTRACT.md) extensions, under `Product.api_base`.

## Per-product identity at build time

- **RAUC `compatible`** is `<product>-rpi4` (override with
  `RAUC_COMPATIBLE`), stamped into the image's `system.conf` and every
  bundle manifest, hotfixes included, so a device only installs its own
  product's bundles. For `slideannouncer` that is the string devices
  already carry. Existing devices of another product can't be retargeted
  by update — changing a product's `compatible` means reflashing.
- **Version suffix**: the local-app version is
  `<X.Y.Z>-<platform hash>-<product>.<product hash>[-dirty]`, where the
  product hash is the last commit touching `PRODUCT_ROOT`
  (`PRODUCT_VERSION_SUFFIX` overrides). It is informational: updates compare
  only `X.Y.Z`, so a product-only change reaches devices only when
  `local-app/VERSION` is bumped. Final image/bundle files are named
  `<product>-<OS version>.*`.

## Image seams — `<product>/image/`

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

`<product>/backend/__init__.py` exposes `product = Product(...)`:

| field | purpose |
|---|---|
| `api_base` | server path prefix of the device contract |
| `routers` | FastAPI routers mounted on the app |
| `background_tasks` | coroutine functions run beside the heartbeat |
| `status_fields()` | extra keys in `GET /api/local/status` |
| `heartbeat_payload()`, `on_heartbeat_response()` | the contract's extension points |
| `wipe_paths` | state wiped on unpair/revocation |

The release's `PRODUCT` file picks the product at run time. Core modules must call `product.get()` at the point of use, never import a
product at module level (products import core).

## Frontend seam — `src/product.js`

`<product>/frontend/index.js` default-exports:

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
  `slideannouncer` user and `slideannouncer.yaml` are shared by every
  product.
- `locales/en.json` and `es.json` still hold the signage strings (menu,
  SRT, Revelation); products can't yet contribute their own messages.
- The setup wizard's steps are fixed (Welcome → Network → Name → Pairing →
  Done); the pairing step assumes the contract above.
- `firstboot.py` keeps its own copy of the pairing wipe list (it runs
  standalone, as root), including signage paths.
- Core Python modules still carry signage-flavoured comments and
  docstrings, and `README.md` files describe the original layout.
