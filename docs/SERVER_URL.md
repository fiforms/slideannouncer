# Choosing the server on the device (first-run wizard)

*Added in local-app 0.5.0 on branch `server-url-first-run`. Everything here is
opt-in per product: a product that doesn't set `confirm_server_url` behaves
exactly as before.*

## The idea

Which management server a device talks to (pairing, heartbeat, sync, OTA
updates, and a product's own traffic such as print jobs) used to be read, on
every request, from `server_url` in `/boot/firmware/slideannouncer.yaml`.
That suits a fleet provisioned by whoever builds the image. It does not suit a
product where the person setting a device up chooses the site.

A product can now ask the first-run wizard to **confirm the server**:

1. `slideannouncer.yaml`'s `server_url` (if present) is only the **default**
   the wizard's Server step pre-fills.
2. The person confirms or changes it. The backend checks it (`GET {url}/up`),
   normalises it, and saves it to **`/data/status/server-url`**.
3. From then on that file is **the** server address: `pairing.read_server_url()`
   and the update CLI (`system/scripts/rauc-update.py`) prefer it to the yaml.
4. It is **never changed from the device again.** `POST /api/local/server-url`
   answers 409 once the device is paired or has finished setup. Editing the yaml
   afterwards does nothing to a device that has confirmed a server. A **factory
   reset** (which reformats `/data`) clears it with everything else and runs the
   wizard again, offering the yaml's value as the default once more. An unpair or
   revocation keeps it (it isn't pairing state).

The yaml's own comment says all of this, since it's the file people edit.

Because the address can now be chosen on the device, nothing about it needs to
be in the root filesystem: `build.sh` seeds `SLIDE_ANNOUNCER_SERVER_URL` into
the **boot partition's** yaml only (see "Build change" below).

## What changed in the platform (for projects that consume it)

Nothing below changes behaviour for a product that leaves
`confirm_server_url` at its default `False`, with one exception noted under
*Build change*.

### Backend (`local-app/backend/`)

| File | Change |
|---|---|
| `product.py` | New field `Product.confirm_server_url: bool = False`. |
| `pairing.py` | New `SERVER_URL_FILE` (`/data/status/server-url`), `normalize_server_url()`, `ServerUrlError`, `read_confirmed_server_url()`, `read_boot_server_url()`, `write_server_url()`, `server_url_locked()`. `read_server_url()` now returns the confirmed URL if there is one, else the yaml's as before (same errors when neither exists). |
| `server_check.py` | `probe(url)` split out of `check()` so the wizard can validate an address before saving it. |
| `main.py` | `GET /api/local/status` gains `confirm_server_url`, `server_url_default`, `server_url_confirmed`, `server_url_locked` (`server_url` is still the effective address). New `POST /api/local/server-url` `{url, verify=true}` → 404 if the product doesn't confirm the server, 409 if locked, 422 for a bad or (when `verify`) unreachable address, else saves and returns `{server_url, check}`. `verify: false` is the wizard's "use it anyway". |
| `test_server_url.py` | New tests for all of the above, including the update CLI. |

`SERVER_URL_FILE` is deliberately **not** in `WIPE_PATHS`.

### Update CLI (`system/scripts/rauc-update.py`)

`read_server_url()` has its own copy of the rule (it runs outside the backend):
`/data/status/server-url` first, then the yaml. **Keep the two in step.** The
other updaters (`os-updater.py`, `update-check.py`, the local-app updater)
reach the address through it or through the backend, so need no change.

### Frontend (`local-app/frontend/src/`)

- New **`setupSteps`** field in the product seam (`product.js`, default `[]`).
  Each entry is `{ key, after, path, component }`: `after` is the core step it
  follows (`welcome`, `network`, `name` or `pairing`), `path` is relative to
  `/setup`, `key` is shown on the progress bar as `setup.steps.<key>` (supply it
  in the product's `messages`).
- New **`setupFlow.js`** computes the step order. The core steps (`SetupWelcome`,
  `SetupNetwork`, `SetupName`, `SetupPairing`, `SetupDone`), `SetupLayout.vue`
  and `router.js` ask it where Back/Next go instead of hard-coding each other's
  paths; with no product steps the behaviour is identical.

A product step is an ordinary wizard page: render `<WizardNav>` with
`:back="prevPath(key)"` and `@next` → `router.push(nextPath(key))`.

### Build change (`image-builder/`) — affects every product

`SLIDE_ANNOUNCER_SERVER_URL` used to be written into
`provisioning/slideannouncer.yaml.example`, which `00-run.sh` installs **twice**:
as `/boot/firmware/slideannouncer.yaml` and into the root filesystem under
`/opt/slide-announcer/provisioning/`. Now `build.sh` writes it to a separate
`boot-seed/server_url` file, and `00-run.sh` applies it to the **boot
partition's** copy only. The rootfs copy of the example keeps the commented
placeholder. Devices behave the same (the boot yaml still has the value); only
the rootfs no longer contains it. (`ssh_authorized_keys` seeding is unchanged.)

### Docs and config

`provisioning/slideannouncer.yaml.example` (the `server_url` comment — see
above), `image-builder/.env.example`, `docs/BUILDING.md`, `docs/PRODUCTS.md`,
`docs/DEVICE_CONTRACT.md`, `image-builder/README.md`, this file.

## Adopting it in a product

1. `Product(..., confirm_server_url=True)`.
2. Frontend `index.js`: add a `setupSteps` entry, normally
   `{ key: 'server', after: 'network', path: 'server', component: SetupServer }`
   (after Network, since checking the address needs the device online and before
   Pairing, which needs it), plus `setup.steps.server` in `messages`. The step
   reads `server_url_default` / `server_url` from `/api/local/status` and posts
   to `/api/local/server-url`.
3. Do **not** bake the destination into `product.env`. For a product whose main
   screen is a remote site, leave `KIOSK_URL` unset so Chromium opens the local
   app (`http://localhost/kiosk`), which runs the wizard on a fresh device and
   then navigates to the site once setup is complete. (A remote `KIOSK_URL`
   skips the wizard entirely — including Wi-Fi setup — because the wizard
   redirect only happens inside the local app.) `portal/` in the R Station
   product is a worked example.

## Not changed / limitations

- A product whose site is down once the kiosk has navigated away gets back to
  Settings with the escape hatch, which the product must opt into — see
  [ESCAPE_HATCH.md](ESCAPE_HATCH.md).
- Moving a set-up device to another server is a factory reset by design; there
  is no in-place "change server".
- Devices already in the field (paired before this change, no
  `/data/status/server-url`) keep using the yaml; nothing migrates them.
