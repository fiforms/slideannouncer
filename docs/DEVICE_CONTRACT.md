# Device management contract (v1)

What every product built on this platform speaks to its management server,
regardless of what the product does. The device side is `pairing.py` and
`heartbeat.py` in `local-app/backend/`; a product's server must implement
the endpoints below under its `api_base` (`Product.api_base`, e.g.
`/api/slide-announcers`). Products extend the contract with their own data —
see [Extensions](#extensions) — without changing the core shape.

This documents the wire format as implemented today (the AnnouncementSlides
server's `SlideAnnouncer*Controller`s are the reference server). Names in
**bold** are renames the contract wants that the reference server doesn't
send yet; the device accepts both.

All bodies are JSON. `{base}` is `{server_url}{api_base}`, where
`server_url` comes from `/boot/firmware/slideannouncer.yaml`.

## Identity

A device has a stable `device_uuid` and `mac_address` (read from the
firstboot-provisioned identity, `identity.py`) and, once paired, a bearer
token. Authenticated calls send `Authorization: Bearer <token>`.

## Pairing — `POST {base}/pair` (unauthenticated)

A person generates a 6-digit, short-lived code in the management UI and
types it on the device.

Request:

| field | |
|---|---|
| `code` | the 6-digit code |
| `device_name` | name typed on the device |
| `mac_address`, `device_uuid` | identity, nullable |
| `language` | optional hint (two/three-letter code) |

Response `200`:

| field | |
|---|---|
| **`device_id`** (legacy `slide_announcer_id`) | server id of the device |
| `entity_id`, `entity_name` | the owner (organization/site) it joined |
| `token` | bearer token for every later call |
| `sibling_hostnames` | hostnames already used under that owner, so the device picks a unique one |
| `language`, `language_revision` | current shared language, if any |

Errors the device surfaces to the user: `422` bad/expired code, `429`
rate-limited. Re-pairing an already-known `device_uuid` re-attaches the same
record (a re-pair, not a duplicate).

## Heartbeat — `POST {base}/heartbeat` (authenticated, every 5 minutes)

One channel carries device status up, and settings and update availability
down.

Request (core fields):

| field | |
|---|---|
| `app_version`, `os_version`, `architecture` | what's installed |
| `cpu_temp_c` | nullable |
| `hostname` | LAN name |
| `language_change` | `{code, base_revision}` when the language was picked on the device and not yet acknowledged |
| *extension fields* | see below |

Response `200`:

| field | |
|---|---|
| `device_name`, `entity_name` | server-authoritative; the device mirrors them |
| `language`, `language_revision` | the settled value (a newer web edit beats a stale device pick) |
| `latest_app_version`, `app_update_available`, `app_download_url` | local-app update tier |
| `latest_os_version`, `os_update_available`, `os_bundle_url`, `os_release_type`, `os_auto_update_enabled` | OS image tier |
| *extension fields* | see below |

`401` means the server revoked the device: the device wipes its pairing
state and the product's, and reboots into setup. Any other failure
(network, 5xx) is recorded and retried; a device keeps working from its
local cache.

## Extensions

A product adds data in three places, all flat keys alongside the core ones
(core keys win a collision on the request; see `heartbeat._CORE_KEYS`):

1. **Heartbeat request** — `Product.heartbeat_payload()` is merged in.
2. **Heartbeat response** — the full body is passed to
   `Product.on_heartbeat_response(response)`, which applies whatever
   settings the server pushed. This is how any per-device settings reach
   the device within one interval.
3. **Product endpoints** under `{base}/…`, reached with
   `pairing.api_url("path")` and the same bearer token (the signage product
   adds `GET {base}/shows` for its slide sync and
   `GET {base}/widget-data/…`).

New products should namespace their keys (`pos_*`, `print_*`) so they can't
collide with each other or later core additions. The signage product's
`srt_sink_*` keys predate that rule and stay as they are.

## Not yet generic on the server

The reference server implements this contract only under
`/api/slide-announcers` and with signage-shaped records. Making the server
side product-agnostic (a shared device table plus per-product extension
data) is the work that makes this contract real for a second product.
