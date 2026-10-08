# Device management contract (v1)

What every product built on this platform speaks to its management server,
regardless of what the product does: **identity, pairing, heartbeat, and
software updates.** Nothing here is specific to slides, widgets or any other
product feature — those are [extensions](#extensions) layered on top.

This file is written so a second implementation of the *server* (a
"management console") can be built from it alone. A machine-readable version
of the same wire format is in [device-contract.openapi.yaml](device-contract.openapi.yaml);
if the two ever disagree, fix both. The device side is `pairing.py` and
`heartbeat.py` in `local-app/backend/`, plus the updaters in `updater/` and
`system/scripts/`. The AnnouncementSlides Laravel server
(`SlideAnnouncer*Controller`, `SlideAnnouncerRelease`) is the reference
implementation.

Key words MUST / SHOULD / MAY are used in the RFC 2119 sense. "Device" is
the kiosk; "server" is the management console.

Names in **bold** are renames the contract wants that the reference server
doesn't send yet; the device accepts both.

## Conventions

- All request and response bodies are JSON (`Content-Type: application/json`).
- `{base}` = `{server_url}{api_base}`. `server_url` comes from
  `/boot/firmware/slideannouncer.yaml` on the device (no trailing slash);
  `api_base` is fixed per product (`Product.api_base`, e.g.
  `/api/slide-announcers`). One image can therefore talk to any server.
- HTTPS is strongly recommended. The bearer token and every update URL travel
  over it. A captive portal intercepting TLS shows up on the device as
  "server unreachable".
- Timestamps, where they appear, are ISO 8601.
- Unknown fields MUST be ignored by both sides — this is what lets the
  contract grow and products extend it.
- The device uses a 10 s request timeout. Respond faster than that.

## Endpoints at a glance

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `{server_url}/up` | none | liveness probe (note: **not** under `api_base`) |
| `POST` | `{base}/pair` | none | exchange a 6-digit code for a device token |
| `POST` | `{base}/heartbeat` | bearer | status up; settings and update offers down |
| — | update URLs (`app_download_url`, `os_bundle_url`) | none | artifact downloads |

Product-specific endpoints live under `{base}/…` and use the same bearer
token (see [Extensions](#extensions)).

## Identity and authentication

- `device_uuid` — stable unique id, created at first boot (`identity.py`),
  survives re-pairing; the server's key for "the same physical device".
- `mac_address` — informational.
- **Token** — an opaque bearer string issued at pairing. The device stores it
  and sends `Authorization: Bearer <token>` on every authenticated call. The
  server SHOULD store only a hash of it, and MUST be able to revoke it.
- A token identifies exactly one device record. A token belonging to a
  non-device principal (e.g. a human user's) MUST be rejected with `403`.
- **Revocation:** a revoked device record answers every authenticated call
  with `401` (see [Revocation](#revocation)).

## `GET {server_url}/up`

Unauthenticated liveness check, hit by the device's Settings → Network screen
to tell "internet is down" from "server is down". Return `200` only if the
application is genuinely up (not merely something listening on the port).
Any other status or a network error is shown as a server problem. Optional
but strongly recommended.

## Pairing — `POST {base}/pair`

A person generates a 6-digit, short-lived, single-use code in the management
UI (scoped to the owner — the "entity" — the device will join) and types it
on the device.

Request:

| field | type | |
|---|---|---|
| `code` | string, exactly 6 chars | required |
| `device_name` | string ≤255 | required; name typed on the device |
| `mac_address` | string ≤255 | nullable |
| `device_uuid` | string ≤255 | nullable (but always sent by real devices) |
| `language` | string ≤10 | optional hint, ISO 639 code; only seeds the device's language if none is set server-side — never overrides an admin's choice |

Response `201` (`200` is also accepted by the device):

| field | |
|---|---|
| **`device_id`** (legacy `slide_announcer_id`) | server id of the device record |
| `entity_id`, `entity_name` | the owner (organization/site) it joined |
| `token` | bearer token for every later call |
| `sibling_hostnames` | hostnames of *other* devices under the same owner, so the device can pick a unique LAN hostname. May be empty; devices that have never heartbeated have no hostname on file and are simply absent |
| `language`, `language_revision` | current shared language (or `null`) and its revision — see [Language](#language) |

Errors the device surfaces to the user: `422` (bad/expired/used code; use one
generic message so existing codes aren't leaked), `429` (rate limited). Any
other `>=400` shows a generic failure.

Server requirements:

- The code MUST be single-use and expire (the reference server: minutes).
- The endpoint MUST be rate-limited — the reference server combines a
  per-IP throttle (10/min) with a longer backoff counter (more than 20
  attempts per IP in 10 minutes → `429`), because a bare throttle doesn't
  stop a slow, patient guesser.
- **Re-pairing:** if `device_uuid` matches an existing record, re-attach that
  record rather than creating a duplicate: update owner/name, clear any
  revocation, **delete all of its old tokens**, then issue a new one. This is
  how a device moves to another site or recovers after being unpaired.
- On success mark the code used and record who created it / which device used
  it.

After a successful pair the device stores the token and device name, mirrors
`entity_name` and language, and derives its hostname from `device_name`
(slugified), appending `-2`, `-3`, … until it isn't in `sibling_hostnames`.

## Heartbeat — `POST {base}/heartbeat`

Authenticated. Sent every **5 minutes** (a first one shortly after boot).
One channel carries device status up, and settings and update offers down.
There is no server push; a change made in the console reaches the device on
its next heartbeat (≤5 min).

### Request

| field | type | |
|---|---|---|
| `app_version` | string \| null | installed local-app version |
| `os_version` | string \| null | installed OS image version |
| `architecture` | string \| null | `platform.machine()` verbatim (`aarch64`, `armv7l`, …) — free-form; it is the key release builds are matched on |
| `cpu_temp_c` | number \| null | SoC temperature |
| `hostname` | string \| null | LAN (mDNS) name, so the console can show how to reach the device |
| `language_change` | `{code, base_revision}` \| absent | present only while a language picked on the device is unacknowledged — see [Language](#language) |
| *extension fields* | any | see [Extensions](#extensions) |

All core fields are optional; a first-ever heartbeat may report null
versions/architecture. The server MUST tolerate that (it simply matches no
release — nothing is offered).

Version strings: see [Versions](#versions).

### Server handling

1. Authenticate; `401` if revoked, `403` if the token isn't a device's.
2. Apply `language_change` if its `base_revision` still equals the stored
   `language_revision`.
3. Update the device's snapshot: versions, architecture, hostname,
   temperature, request source IP, `last_seen_at = now`. A missing field
   MUST leave the stored value alone rather than blanking it.
4. SHOULD append a row to a rolling heartbeat log (the reference server keeps
   version, IP and temperature per beat) for fleet history.
5. Resolve update offers ([Updates](#updates)) and respond.

### Response `200`

| field | type | |
|---|---|---|
| `ok` | bool | `true` |
| `device_name` | string | **server-authoritative.** The device overwrites its local name with this, so a rename in the console reaches the device |
| `entity_name` | string | likewise authoritative (also covers a re-pair into another owner) |
| `language`, `language_revision` | string\|null, int | the settled value — see [Language](#language) |
| `latest_app_version` | string \| null | version tagged for this device's channel + architecture, or null |
| `app_update_available` | bool | |
| `app_download_url` | string \| null | non-null only when `app_update_available` |
| `app_sha256` | string \| null | hex SHA-256 of the app archive; only when available |
| `latest_os_version` | string \| null | |
| `os_update_available` | bool | |
| `os_bundle_url` | string \| null | non-null only when `os_update_available` |
| `os_bundle_sha256` | string \| null | informational; the device relies on RAUC's signature check instead (see [Updates](#updates)) |
| `os_release_type` | `"full"` \| `"hotfix"` \| null | non-null only when `os_update_available` |
| `os_auto_update_enabled` | bool | per-device switch: whether the device may apply OS updates on its own schedule |
| *extension fields* | any | see [Extensions](#extensions) |

The device caches the entire response body locally (`/data/status/heartbeat.json`)
and its updaters read the update fields from that cache — they make no
network call of their own until an update actually exists.

### Failure behavior

- Network error, timeout, `5xx`, other `4xx`: recorded as the last error,
  retried at the next interval. The device keeps running from its local
  cache. The server SHOULD NOT mark a device offline merely because a beat is
  late — judge by `last_seen_at` age (the reference console treats a few
  missed intervals as "offline").

### Revocation

`401` on **any** authenticated call means the server revoked this device. The
device then deletes its token and pairing state (and the product's, via
`Product.wipe_paths`) and **reboots into the setup flow**. Therefore:

- Only return `401` for a genuinely revoked/unknown token. Never use it for
  transient problems.
- To un-pair a device from the console, mark it revoked (and/or delete its
  tokens). The device finds out at its next heartbeat.
- Re-pairing the same `device_uuid` clears the revocation (see Pairing).

## Language

One shared language setting per device, editable on the device (setup wizard /
settings) *and* in the console, last write wins, resolved with a revision
counter so neither side silently clobbers the other.

- Server stores `language` (a code, nullable) and an integer
  `language_revision` per device. Every change of `language` — from either
  side — increments the revision.
- Device sends `language_change {code, base_revision}` when the user picked a
  language locally; `base_revision` is the `language_revision` the device had
  last seen when it picked.
- If `base_revision == stored language_revision`, the server applies the
  change (revision +1). Otherwise a web edit happened in between; the server's
  value wins and the device's pick is dropped.
- The response always carries the settled `language` + `language_revision`;
  the device adopts them and clears its pending pick. `language: null` means
  nobody has set one yet, so the device keeps its boot-config default.
- Unknown codes in `language_change` are ignored (no error).

Servers that don't want a language feature MAY always return
`language: null, language_revision: 0` and ignore `language_change`.

## Updates

Two independent update tiers, both **offered** by the heartbeat response and
**pulled** by the device. The server never pushes.

| tier | `kind` | artifact | applied by |
|---|---|---|---|
| local app (frequent, low risk) | `app` | `.tar.gz` | `updater/local_app_updater.py`; swaps a symlink, restarts services, auto-reverts if its health check fails |
| OS image (rare, A/B + rollback) | `os` | RAUC bundle `.raucb` | `system/scripts/os-updater.py`; `rauc install <os_bundle_url>` |

The device decides *when* to apply (an idle window, default 02:00–05:00
local, or immediately when a person clicks "Update" on the device), and never
applies an OS and an app update in the same pass (OS first). A version that
failed to install is not retried every cycle — only once the offered version
changes. The server therefore only has to offer the right release; it doesn't
track progress. (There is currently no update-result report back to the
server beyond the versions in the next heartbeat.)

### Releases (server data model)

A console needs a catalogue of uploaded builds. Minimum fields (reference:
`SlideAnnouncerRelease`):

| field | |
|---|---|
| `kind` | `app` \| `os` |
| `version` | see [Versions](#versions) |
| `architecture` | free-form, matched exactly against the device's reported `architecture` |
| `release_type` | `full` — complete app archive or OTA image; `hotfix` — OS only, a small bundle that applies only on top of exactly one prior version; `disk_image` — OS only, a flashable `.img.xz` for re-imaging an SD card, **never offered over the heartbeat** |
| `required_base_version` | hotfix only: the exact version it applies on top of |
| `sha256` | hex digest of the stored artifact |
| artifact location | a URL the device can GET |
| notes | free text, optional |

### Channels

Each device has an `update_channel` (reference: `stable`, `testing`,
`developer`), set in the console and **not** reported by the device. Releases
are *tagged* onto channels; a channel's tagged release is "current" for it. A
release tagged on no channel is archived. A device sees only what is tagged on
its own channel.

Tagging occupies one **slot** per `(kind, architecture, channel)` for the
`full` release, plus one slot per distinct `required_base_version` among
hotfixes (and one for `disk_image`). Tagging a release evicts only the sibling
in the *same* slot — so a full release and several hotfixes for different
base versions can be live on a channel at once.

### Resolution (per heartbeat, per kind)

For a device with `(channel, architecture, current_version)`:

1. If `current_version` is known: look for a tagged `hotfix` on this channel,
   kind and architecture whose `required_base_version == current_version`
   exactly. If found, that's the candidate.
2. Otherwise the candidate is the tagged `full` release for
   `(kind, architecture, channel)`.
3. No candidate → `latest_*_version: null`, `*_update_available: false`.
4. Is it an update?
   - candidate is a `hotfix` → yes iff `candidate.version != current_version`
     (its base-version match already rules out direction ambiguity).
   - candidate is `full` → yes iff `current_version` is null or
     `compare(candidate.version, current_version) > 0`. Equal or lower
     (a downgrade, or an admin tagging an older build) is **not** offered.
5. If yes, fill the `*_download_url` / `*_bundle_url`, `*_sha256`, and (OS)
   `os_release_type` fields; otherwise leave them null.

Multi-step upgrades resolve one hop per heartbeat: after a hotfix lands, the
device's reported version changes and the next heartbeat may match another
hotfix or the full release.

### Versions

`X.Y.Z`, optionally a **pair** `<platform X.Y.Z>_<product X.Y.Z>` (e.g.
`0.4.0_0.1.1`). A plain `X.Y.Z` counts as product `0.0.0`.

Compare as the six-integer array `[platform.x, y, z, product.x, y, z]`, i.e.
platform first, then product. Only the leading version is parsed — whatever
follows is ignored (an app version reported by a device carries a git-hash
suffix). Strings that don't parse fall back to a generic natural version
compare (PHP `version_compare`). `hotfix` matching uses plain **string
equality** against the device's reported version, so reported and stored
strings must be byte-identical.

Uploaded filenames may be parsed to prefill the console form (optional, never
blocking): `slideannouncer-<ver>.raucb`,
`slideannouncer-<ver>.hotfix.from.<base>.raucb`,
`slide-announcer-local-app-<ver>[-…].tar.gz`.

### Artifact serving

- Update URLs are fetched by the device with **plain unauthenticated GETs**
  (no bearer token). They MUST therefore be absolute URLs reachable from the
  device's network, and SHOULD be unguessable or otherwise non-enumerable if
  the builds are sensitive. Redirects are followed.
- `.raucb` URLs are passed to `rauc install`, which streams the bundle over
  HTTP(S) and needs **HTTP range support** for efficient streaming (any
  static file host or object store provides it). RAUC verifies the bundle's
  signature against the device's keyring, so a tampered or unsigned bundle is
  refused regardless of `os_bundle_sha256`.
- App archives are downloaded whole; the device verifies `app_sha256` when it
  is present and aborts on mismatch. Always send it.
- Large files should be served by a CDN/object store, not through the
  application server.

## Extensions

A product adds data in three places, all flat keys alongside the core ones
(core keys win a collision on the request; see `heartbeat._CORE_KEYS`):

1. **Heartbeat request** — `Product.heartbeat_payload()` is merged in.
2. **Heartbeat response** — the full body is passed to
   `Product.on_heartbeat_response(response)`, which applies whatever settings
   the server pushed. This is how per-device settings reach the device within
   one interval.
3. **Product endpoints** under `{base}/…`, reached with
   `pairing.api_url("path")` and the same bearer token.

New products should namespace their keys (`pos_*`, `print_*`) so they can't
collide with each other or later core additions. Product-specific endpoints
and data (for example a signage product's slide sync) are **not** part of
this contract.

A server that doesn't know a product's extension keys can ignore them in
requests and omit them from responses; a device must keep working.

## Minimum viable server

To manage a fleet of devices a server needs only:

1. `POST /pair` with single-use expiring codes, re-pairing by `device_uuid`.
2. `POST /heartbeat` that authenticates, stores the snapshot, and returns
   `device_name`, `entity_name`, `language: null`, `language_revision: 0`,
   and the update fields (all-null / `false` is valid: "nothing to offer").
3. Token revocation → `401`.

Everything else (`/up`, channels, hotfixes, `os_auto_update_enabled`,
heartbeat history) is additive.

## Not yet generic on the server

The reference server implements this contract only under
`/api/slide-announcers`, in tables named for the signage product. Its
heartbeat controller is already core-only: product keys plug in through the
`HeartbeatExtension` interface (`app/Support/SlideAnnouncer/`), with the
signage video receiver as the one registered extension.
Making the server side product-agnostic (a shared device table plus
per-product extension data) is the work that makes this contract real for a
second product. Open contract items: the `device_id` rename, and reporting
update outcomes (success/failure/progress) back to the server.
