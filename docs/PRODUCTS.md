# Products: one platform, many kiosks

This repo is a kiosk **platform** — the OS image, Wi-Fi/settings shell,
first-run setup, pairing, heartbeat, and two-tier updates — with the
signage behavior layered on as a **product**. A different kiosk (a point of
sale pointed at a remote web app, say) reuses everything but the product.

Product = one directory, **outside this repo**, named by `PRODUCT_ROOT`:

```
<product>/
  image/      product.env, packages, requirements.txt, enable, system/      → the OS image
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
- **Versions are pairs, `<platform>_<product>`.** A product has two optional
  version files, tracked separately:
  - `PRODUCT_ROOT/VERSION` — the product's **app** (its backend and frontend).
  - `PRODUCT_ROOT/image/VERSION` — the product's **OS-level** files
    (the `image/` seam: units, scripts, nginx, packages).

  The platform has its own pair of files, and each pipeline pairs like with
  like:

  | Artifact | Version | Example |
  |---|---|---|
  | Local app (tarball, `/data/local-app/current/VERSION`) | `local-app/VERSION` + product `VERSION` | `0.4.0_0.1.1` |
  | OS image, bundle, hotfix (`/opt/slide-announcer/VERSION`) | `image-builder/VERSION` + product `image/VERSION` | `0.4.1_0.1.0` |

  So a product-only change is its own version (`0.4.0_0.1.0` →
  `0.4.0_0.1.1`, nothing changes in the platform) and so is a platform-only
  change (`0.4.0_0.1.0` → `0.4.1_0.1.0`). The separator is an underscore so
  the string is safe in file names and URLs. A product with no version file
  has a plain platform version. A device from before pairs existed reports a
  plain `X.Y.Z`, which counts as product `0.0.0`.
- **App version string**: `<platform>_<product>-<platform hash>-<product>[-<hash>]`,
  e.g. `0.4.0_0.1.1-8066cfc-slideannouncer-e4b6392`, where the hash is the last
  commit touching `PRODUCT_ROOT` (`-dirty` for uncommitted changes;
  `PRODUCT_VERSION_SUFFIX` overrides the part after the pair). Only the
  leading pair is compared — platform first, then product — by the device
  (`local-app-seed.py`, `updater/local_app_updater.py`) and by the server
  (`SlideAnnouncerRelease::compareVersions`). OS versions are matched
  **exactly** (hotfix gating, `os-updater.py`). Final image/bundle files are
  named `<product>-<OS version>.*`, e.g. `slideannouncer-0.4.1_0.1.0.raucb`.

## Image seams — `<product>/image/`

- `product.env` — shell settings sourced by `kiosk-start.sh`. (Don't point
  `KIOSK_URL` at a remote site if the device needs the first-run wizard —
  see *A remote-portal product* below.) `KIOSK_URL` is
  what Chromium opens: `http://localhost/kiosk` (the local app) or a remote
  https URL.
- `packages` — extra apt packages, appended to the core list at build time
  (CUPS, for instance). Hotfix bundles can't install packages, so changing
  this means a full image.
- `requirements.txt` — optional extra Python dependencies, appended to
  `local-app/backend/requirements.txt` for the image's venv and for
  `run-tests.sh`. The venv is built only at image time, so changing this
  also means a full image.
- `system/*.service|*.timer` — units installed into `/etc/systemd/system`;
  `system/scripts/*` — installed into `/usr/local/sbin` under the same name.
- `enable` — the units to enable, one per line.
- `splash.png` — optional boot splash, painted on the framebuffer early in
  boot by `slide-announcer-splash.service`. Overrides the core's default
  (`system/splash/splash.png`); scaled to fit, centred on black, so use a
  black background. Can ship as a hotfix (the image is pre-scaled at build time; the device needs no image library).
- `BOOT_COMMENT` — optional free text, written as `#` comments at the top of
  the boot partition's `slideannouncer.yaml` (with product, OS version and
  build date) so someone mounting the card offline can tell what it is.

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
| `confirm_server_url` | the server address is confirmed in the first-run wizard and saved on the device (`SERVER_URL.md`) |

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
| `setupSteps` | extra first-run wizard steps, `{ key, after, path, component }` (`setupFlow.js`; `SERVER_URL.md`) |
| `setupDoneNote` | component shown on the wizard's Done screen (e.g. the R Station's escape-key hint) |
| `loadSettings()` | refresh product state when Settings opens |
| `settingsPin()` | the Settings PIN, or `null` |
| `messages` | `{ en, es }` UI strings, deep-merged over the core `locales/*.json` (product wins on conflict) |

Product code imports core through the `@core/` alias (`@core/api.js`,
`@core/components/…`); the product's own `api.js` extends the core `api`.

## A remote-portal product (what a POS needs)

Don't put the portal's address in `KIOSK_URL`: a remote `KIOSK_URL` makes the
device boot straight to the site, so on a fresh device the setup wizard — and
with it Wi-Fi setup — never runs (its redirect only exists inside the local
app). Leave `KIOSK_URL` unset, set `Product.confirm_server_url`, add a wizard
step with `setupSteps`, and make `mainView` a small launcher that navigates to
the confirmed site once setup is done — see [SERVER_URL.md](SERVER_URL.md);
the Settings screens keep working at `http://localhost/…`. The way back from the
remote page is the escape hatch — a key combination, opt-in per product; see
[ESCAPE_HATCH.md](ESCAPE_HATCH.md). **Not built yet:** a Chromium URL allow-list.

## Still signage-flavoured (known leftovers)

- Names: `slide-announcer-*` units, `/opt/slide-announcer`, the
  `slideannouncer` user and `slideannouncer.yaml` are shared by every
  product.
- The setup wizard's steps are fixed (Welcome → Network → Name → Pairing →
  Done); the pairing step assumes the contract above.
- `firstboot.py` keeps its own copy of the pairing wipe list (it runs
  standalone, as root), including signage paths.
- Core Python modules still carry signage-flavoured comments and
  docstrings, and `README.md` files describe the original layout.
