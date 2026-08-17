# Slide Announcer Localization — TODO

Goal: the device's UI and the slides it displays both follow one configured
language, resolvable before pairing (from the boot partition) and overridden
per-device from the server once paired.

## 1. Device-side language resolution (before pairing → after pairing)

- [ ] Add a `language` key to `/boot/firmware/slideannouncer.yaml`
  (alongside the existing wifi/ssid keys `provisioning/firstboot.py`
  already reads via `BOOT_YAML`/`yaml.safe_load`). This is the
  pre-pairing default — someone imaging an SD card for a Spanish-speaking
  congregation sets it once, no pairing required.
- [ ] `firstboot.py` (or a small new `language.py` alongside `pairing.py`)
  reads this value into a local cache file, same pattern as
  `pairing.py`'s `DEVICE_NAME_FILE`/`ENTITY_NAME_FILE` — e.g.
  `/data/status/language.json` with `{"code": "es", "source": "boot_yaml"}`.
- [ ] Once paired, the server is authoritative, same precedence rule
  already established for `device_name`/`entity_name`: heartbeat responses
  win over the boot-yaml default. Add a `language` field to the
  `SlideAnnouncerHeartbeatController::store()` response (server's
  `SlideAnnouncer.language_id` → `Language.abbreviation`), and fold it into
  the local cache in `local-app/backend/heartbeat.py::send_once()` the same
  way `device_name`/`entity_name` are folded in today — write a
  `pairing.write_language()`-style helper if it lives in `pairing.py`, or a
  new module if it doesn't belong there.
- [ ] Decide what happens between "device just imaged, not yet paired" and
  "first successful heartbeat": boot-yaml value applies until overridden,
  never cleared back to boot-yaml after a server value has been received
  (mirrors how `device_name` never reverts).
- [ ] `slide-announcer.yaml`'s `language` key should also be documented in
  `provisioning/README.md` / `docs/` wherever the other boot-yaml keys are
  documented.

## 2. Server: per-device language selector

- [ ] Migration: add `language_id` (nullable FK to `languages`) to
  `slide_announcers` table (see existing
  `database/migrations/2026_08_10_130001_add_architecture_to_slide_announcers_table.php`
  for the pattern of adding a column to this table after the fact).
- [ ] `App\Models\SlideAnnouncer`: add `language_id` to `$fillable`, add a
  `language()` belongsTo relation (mirrors `App\Models\Slide::language()`).
- [ ] `EntitySlideAnnouncerController`: expose `language_id` (and the full
  language list, reusing wherever `Language::all()` is already fetched for
  the slide-submission form) in the `Entity/SlideAnnouncers` Inertia props,
  and accept it in whatever `update()` action already handles rename —
  check for an existing `update` method in that controller before adding a
  new endpoint.
- [ ] Frontend: `resources/js/Pages/Entity/SlideAnnouncers.vue` (or
  equivalent) gets a per-device language `<select>` next to the existing
  device-name control, populated from the same languages list the slide
  upload form uses.
- [ ] Nothing needed in `SlideAnnouncerPairingController` — a freshly
  paired device has `language_id = null` (falls back to boot-yaml default
  until an entity admin sets it).

## 3. Server: filter slide sync by device language

- [ ] `Slide::scopeLanguage()` (`app/Models/Slide.php`) already implements
  exactly this rule for the web slideshow — `whereNull('language_id')` (not
  language-specific, goes to everyone) `orWhere('language_id', $id)`
  (matches the given language). Reuse it, don't reimplement it.
- [ ] `SlideAnnouncerSyncController::index()` currently does:
  ```php
  Slide::where(fn ($q) => $q->whereNull('entity_id')->orWhere('entity_id', $device->entity_id))
      ->current()
  ```
  Add `->language($device->language_id)` to that chain. With
  `language_id = null` (device has no language configured yet),
  `scopeLanguage` already treats that as "no filter" (see the
  `if ($languageId === null) { return $query; }` guard) — confirm that
  early-return exists and behaves as expected for an unset device.
- [ ] Check whatever the *web* slideshow view/controller does for its
  equivalent language scoping — reuse the same `$user`/`$viewer` → language
  resolution logic if there's a shared helper, so device sync and web
  viewing can never drift apart.

## 4. Device-side UI localization

- [ ] Pick an i18n approach for the frontend — nothing is wired up yet
  (`local-app/frontend/src` has no `i18n`/`locale` references currently).
  Vue 3 + `vue-i18n` is the standard fit given `App.vue`/`router.js` already
  use Vue 3 conventions.
- [ ] Scope of strings to translate: `views/PinGate.vue`,
  `views/Slideshow.vue` (any on-screen status/error text, not the slides
  themselves), and everything under `views/settings/` (`System.vue`,
  `Pairing.vue`, `WifiConnect.vue`, `WifiList.vue`, `NetworkStatus.vue`,
  `DeviceTools.vue`, `KeyDebug.vue`, `SettingsLayout.vue`).
- [ ] Wire the resolved language (from step 1's local cache file) into the
  Vue app at boot — `main.js` reads the cache file via a small
  `GET /api/local/status`-style endpoint (or a dedicated one) exposed by
  `local-app/backend/main.py`, and sets the active `vue-i18n` locale before
  mount.
- [ ] Settings UI needs a way to *see* the current language and its source
  (boot-yaml default vs. server-assigned) — likely on `System.vue` next to
  other device info, read-only (language is set from the server per step
  2, not locally, to keep one source of truth per SLIDE_ANNOUNCER.md's
  general "server is authoritative once paired" pattern).
- [ ] Only need to support the languages that exist in the `languages`
  table today — check `Language::all()` / the languages seeder for the
  current list before translating strings for anything wider.

## Decisions

- Boot-yaml `language` is only ever a pre-pairing hint. The server value
  always wins once paired — never reverts to boot-yaml while paired, even
  across a re-pair to a different entity (mirrors `device_name`/
  `entity_name`).
- Factory reset reverts to the boot-yaml language (it's baked into the
  image/boot partition, so this falls out naturally as long as the reset
  path doesn't try to preserve the old language cache).
- Unpair (`pairing.py::unpair_and_wipe()`): no hard requirement either way
  — implement whichever is simpler. Given `WIPE_PATHS` already deletes
  `DEVICE_NAME_FILE`/`ENTITY_NAME_FILE` unconditionally, the simplest
  option is adding the language cache file to that same list so it's wiped
  too and falls back to reading boot-yaml fresh on next boot — consistent
  with the factory-reset behavior above without extra special-casing.
