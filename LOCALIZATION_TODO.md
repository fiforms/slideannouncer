# Slide Announcer Localization — TODO

Goal: the device's UI and the slides it displays both follow one configured
language, resolvable before pairing (from the boot partition) and overridden
per-device from the server once paired.

## 1. Device-side language resolution (before pairing → after pairing) — done

- [x] Added a `language` key read from `/boot/firmware/slideannouncer.yaml`
  by `provisioning/firstboot.py::write_language_boot_hint()`, cached to
  `/data/status/language-boot-hint.json` every boot.
- [x] `local-app/backend/pairing.py` gained `LANGUAGE_FILE` (server-assigned
  cache, same pattern as `DEVICE_NAME_FILE`/`ENTITY_NAME_FILE`),
  `read_language()`/`write_language()`/`read_language_boot_hint()`, and
  `read_effective_language()` (server value if present, else the boot-yaml
  hint).
- [x] `local-app/backend/heartbeat.py::send_once()` folds a `language` field
  from the heartbeat response into `pairing.write_language()`, same as
  `device_name`/`entity_name`.
- [x] Precedence confirmed: server always wins once paired, never reverts
  to the boot-yaml hint while paired.
- [ ] Still open: document the boot-yaml `language` key in
  `provisioning/README.md` / `docs/` alongside the other boot-yaml keys.

## 2. Server: per-device language selector — done

- [x] Migration `2026_08_16_100000_add_language_id_to_slide_announcers_table.php`
  adds nullable `language_id` FK to `slide_announcers`.
- [x] `App\Models\SlideAnnouncer`: `language_id` fillable, `language()`
  belongsTo relation.
- [x] `EntitySlideAnnouncerController`: exposes `languages` list on
  `index`/`show`, accepts `language_id` in `update()`, included in
  `deviceResource()`.
- [x] `resources/js/Pages/Entity/SlideAnnouncerShow.vue` has a language
  `<select>` in the device settings form (unset = "use the device's own
  default").
- [x] `SlideAnnouncerPairingController` untouched — a freshly paired device
  has `language_id = null`.

## 3. Server: filter slide sync by device language — done

- [x] `SlideAnnouncerSyncController::index()` chains
  `->language($device->language_id)` onto its existing query, reusing
  `Slide::scopeLanguage()` (which already no-ops on a null `language_id`).
- [x] `SlideAnnouncerHeartbeatController::store()` returns
  `'language' => $device->language?->abbreviation` so the device backend
  above has something to fold in.

## 4. Device-side UI localization — done

- [x] `vue-i18n` added to `local-app/frontend` (mirrors the main app's
  `resources/js/i18n.js` setup: composable `useI18n()`/`t()` per component,
  flat `en`/`es` JSON messages, `fallbackLocale: 'en'`).
- [x] Translated every user-facing view: `PinGate.vue`, `Slideshow.vue`,
  `SettingsLayout.vue`, and everything under `views/settings/` (`System.vue`,
  `Pairing.vue`, `NetworkStatus.vue`, `WifiList.vue`, `WifiConnect.vue`,
  `DeviceTools.vue`, `KeyDebug.vue`). Raw/technical values (IP addresses,
  version numbers, the factory-reset "RESET" safety word, KeyDebug's raw
  event-property table headers) were deliberately left untranslated.
- [x] `main.js` fetches `GET /api/local/status` before the first render and
  calls `i18n.js`'s `setLocale(status.language)`; `Slideshow.vue`'s existing
  15s status poll also calls `setLocale()` on every cycle, so a language
  reassigned by an entity admin reaches the screen within one heartbeat
  interval without a restart.
- [x] `System.vue`'s Device Info block now shows the current language and
  its source ("set by entity admin" vs. "device default").
- [x] Only `en`/`es` are translated (`LanguageSeeder`/the language-tagging
  feature only have English + Spanish today); `setLocale()` clamps any
  other code to `en` via `SUPPORTED_LOCALES`, and vue-i18n's
  `fallbackLocale` covers any translation key gap regardless.

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
