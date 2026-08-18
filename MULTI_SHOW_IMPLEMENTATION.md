# Multi-Show Kiosk Implementation Spec

Status: **not started** — this document specifies backend-confirmed work for
the kiosk (this submodule) that follows a completed backend/web feature in
the main `announcementslides` repo. Read this before starting; it reflects
what the server actually does today, not a proposal.

## Context

The main app replaced flat per-entity slide sorting with named, orderable
**"shows"** (playlists) — see `SLIDE_ANNOUNCER.md` in the main repo for the
full write-up (data model, `show_slides` pivot, global/nearby auto-fill,
per-show language filtering). The pieces that affect this submodule:

- Every entity now has one or more `Show` rows (always includes a
  non-deletable "Main" show) instead of one flat slide list.
- A slide's order and show-membership are fully server-resolved — the kiosk
  no longer needs (or receives) a `sort_order` field; **array order is
  display order**, period.
- Per-device language filtering (`SlideAnnouncer.language_id` used to filter
  which slides a device received) has been **removed server-side**. Shows
  now carry their own language curation; the device's `language_id` field
  still exists but means only "what language should this kiosk's own UI
  chrome/settings be in," not a slide filter. If the frontend currently uses
  it for anything content-related, that usage should be dropped.
- The kiosk should sync **every show belonging to its paired entity**, not
  just one, and let the user pick which one plays via a Menu overlay — this
  was the whole point of building shows in the first place (e.g. a lobby TV
  running the regular rotation vs. a special one-off promo show).

## What already exists server-side (confirmed, not to be re-designed)

### `GET /api/slide-announcers/slides` (legacy, unchanged contract)

Still works exactly as before, mapped to the entity's Main show (which
already contains every applicable global/nearby slide via server-side
auto-fill). Kept working so already-deployed kiosks don't break during
rollout — **build against the new endpoint below; don't rely on this one for
new work.**

```json
{
  "settings": { "...": "device settings, unchanged shape" },
  "slides": [
    {
      "id": 101,
      "file_url": "https://.../slide.jpg",
      "thumbnail_url": "https://.../thumb.jpg",
      "mime_type": "image/jpeg",
      "video_playback_mode": null,
      "overlay_url": null,
      "overlay_mime_type": null,
      "expires_at": null
    }
  ]
}
```

Note there is **no `sort_order` key** — the array is already in display
order.

### `GET /api/slide-announcers/shows` (new — build against this)

Auth: same Sanctum device bearer token as the existing endpoints (route is
registered under the same `auth:sanctum` + `slide-announcer.auth` middleware
group in `routes/api.php`). Returns **every** show belonging to the device's
paired entity (Main plus any custom or globally-distributed one-off shows —
never the entity-less "Global Board," which isn't entity-facing):

```json
{
  "shows": [
    {
      "id": "12",
      "name": "Main Show",
      "is_main": true,
      "slides": [
        {
          "id": 101,
          "file_url": "https://.../slide.jpg",
          "thumbnail_url": "https://.../thumb.jpg",
          "mime_type": "image/jpeg",
          "video_playback_mode": null,
          "overlay_url": null,
          "overlay_mime_type": null,
          "expires_at": null
        }
      ]
    },
    {
      "id": "17",
      "name": "Christmas Promo",
      "is_main": false,
      "slides": [ "..." ]
    }
  ],
  "settings": { "...": "same shape as today" }
}
```

Notes:
- `id` is a **string** on the show objects (matches the existing
  string-cast-id convention already used for slide entries elsewhere in this
  codebase's sync payloads — don't assume it round-trips as a number).
- Exactly one show has `is_main: true`. It cannot be deleted server-side, so
  it's always safe to treat as the guaranteed fallback.
- No show ordering field is provided — sort `shows` however you want to
  display them in the menu (e.g. Main first, then alphabetical), since the
  server doesn't impose an order across shows, only within one show's
  `slides` array.
- Per-slide shape inside each show is identical to the legacy endpoint's
  entries (no `sort_order`, no language field — language curation already
  happened server-side before the response was built).
- A show that's empty (no current slides) is still included in the list —
  don't filter it out; a leader may be about to add content to it, and the
  Menu overlay should still let the user select it (it'll just show nothing
  until slides are added).
- There is **no per-device server-assigned show**. Show selection is
  entirely local to the kiosk (see Pinning below) — this was a deliberate
  simplification over an earlier design sketch in `SLIDE_ANNOUNCER.md` that
  considered a `slide_announcers.show_id` FK; it was dropped in favor of the
  on-device Menu + pin described here.

## Work to build in this submodule

### 1. `backend/sync.py` — multi-show sync

Replace the single-list poll (currently hitting `/slides`) with a poll
against `/shows`. Concretely:

- Fetch `GET /api/slide-announcers/shows` instead of (or in addition to,
  during a transition) `/slides`.
- Restructure the on-disk manifest from a flat `{slide_id: entry}` map to
  `{show_id: {name, is_main, slides: {slide_id: entry}}}`.
- Restructure the on-disk active-playlist file similarly to
  `{"shows": [{id, name, is_main, slides: [...]}]}` — each show's `slides`
  array keeps the exact per-slide entry shape the playback engine already
  expects; only the wrapper changes from a flat list to a list-of-shows.
- Per-slide diff/download/orphan-cleanup logic (deciding what media needs
  downloading vs. is already cached) is unchanged in substance — just run it
  once per show instead of once for a single flat list.
- Also detect shows that disappeared entirely between polls (a show got
  deleted server-side, or the last slide in a one-off promo show expired and
  the server's `shows:prune-empty` swept it) and clean up their cached media
  the same way orphaned slides are cleaned up today.
- **After a successful sync only** (never on a failed/offline poll — an
  offline kiosk must keep playing whatever it last had): if the currently
  pinned show id (see §2) is no longer present in the new show set, clear
  the pin so playback falls back to Main. This is the "leader deleted the
  show I was pinned to" recovery path.

### 2. New `backend/pinning.py` — on-device show selection persistence

Small module mirroring `pairing.py`'s atomic-write convention (temp file +
`os.replace`), same pattern as the existing single-value status files
(`device-name`, `language`, etc.):

- New file: `/data/status/pinned-show-id` — plain text, the show id or
  absent/empty meaning "unpinned, play Main."
- `read_pinned_show_id() -> str | None`
- `write_pinned_show_id(show_id: str | None) -> None` (writing `None`
  deletes the file)
- **Add this path to `pairing.py`'s `WIPE_PATHS`.** Unlike room-property
  settings (`audio-output`, `audio-volume`), which are deliberately excluded
  from wipe-on-unpair because they describe the hardware, not the pairing —
  a pinned show id is meaningless once unpaired (possibly re-pairing to a
  *different* entity's show catalog entirely), so it must be wiped with the
  rest of the pairing state.

### 3. `backend/main.py` — local API additions

- `GET /api/local/slideshow` response grows to include the full `shows`
  list (as fetched/cached by `sync.py`) and a resolved `pinned_show_id`
  (falling back to the Main show's id if the pin is unset or stale — do this
  resolution here even though `sync.py` should already have cleared a truly
  stale pin, as cheap defense-in-depth).
- New `POST /api/local/pin-show`, body `{"show_id": "17"}` or
  `{"show_id": null}` to explicitly pin to Main. Writes via `pinning.py`.
  This is a **local-only** action — no server round-trip, no
  authentication beyond whatever already guards the local API (the main
  Laravel backend has no concept of "what a kiosk currently has pinned").

### 4. Frontend — Menu overlay (new UI pattern for this codebase)

Today, pressing Menu (`remoteNav.js`'s `MENU_KEYS` handler) does
`router.push('/settings')` — a full route navigation. That needs to become
an **overlay on top of whatever's currently showing**, not a route change,
so the slideshow keeps running underneath it and Back can dismiss without
navigating anywhere.

- New `frontend/src/menuOverlay.js`: tiny reactive module (`menuOpen` ref +
  `openMenu()`/`closeMenu()`), same style as the existing `pinLock.js`, so
  plain-JS `remoteNav.js` can toggle it without becoming a Vue component.
- New `frontend/src/views/MenuOverlay.vue`, mounted at the top level in
  `App.vue` **alongside** `<router-view>`, not nested inside it — visibility
  driven purely by `menuOpen`. This is the cleanest way to get a true
  overlay without touching route nesting.
- Styling: full-screen `position: fixed; inset: 0`, semi-transparent dark
  scrim, centered list card. Reuse the existing `--panel`/`--border`/
  `--accent`/`--text` CSS variables and `.list-item` styling from
  `style.css` for visual consistency with the rest of the kiosk chrome.
  TV-viewing-distance sizing (large text/touch targets), consistent with
  the rest of this UI.
- Content: every show from the synced list, current pin (or Main, if
  unpinned) visually marked, followed by **"Settings…" as the last item**,
  visually separated (e.g. a divider) since it's a different kind of action.
- List items should be plain focusable `<button>`s — the existing
  spatial-nav arrow-key/Enter handling in `remoteNav.js` already operates on
  whatever's focusable in the DOM, so this works with **zero changes** to
  the direction-key logic itself.
- Recommend lifting `Slideshow.vue`'s currently-polled state (`shows`,
  `settings`, `pinned_show_id`) into a small shared module (e.g.
  `slideshowState.js`) so `MenuOverlay.vue` doesn't need a duplicate fetch
  and can reflect a pin change instantly (update local state on a
  successful `pin-show` call) instead of waiting up to 60s for the next
  poll.

### 5. `remoteNav.js` changes

- Menu-key handler: if the overlay isn't already open and the user isn't
  already inside `/settings`, call `openMenu()` and `preventDefault()`
  instead of navigating. Pressing Menu while already in `/settings` stays a
  no-op, same as today (Settings is reached *from* the overlay, not the
  reverse).
- Back-key handler: check `menuOpen` first — if open, `closeMenu()` and
  `preventDefault()`, **without** falling through to `router.back()`. This
  is the "Back dismisses without changing the pin" requirement.
- Arrow-key/Enter handling needs **no changes** — focusable overlay buttons
  are automatically included/excluded from `findNext()`'s candidate set
  based on whether `menuOpen` has them rendered.
- Because opening the overlay isn't a route change, `router.afterEach`'s
  existing focus-management hook won't fire for it — `MenuOverlay.vue` needs
  its own `watch(menuOpen, ...)` to focus its first item when it opens,
  rather than teaching `remoteNav.js` about overlay internals.

### 6. `views/Slideshow.vue` changes

- `refreshPlaylist()` (or wherever the polled response is currently turned
  into the on-screen slide list) now selects the *active* show out of the
  multi-show response: `pinned_show_id` if set and present in `shows`, else
  the show with `is_main: true`, else `shows[0]` as a last-resort
  defensive fallback (should never be needed if the backend's own-Main
  guarantee holds, but cheap to have).
- The per-slide playback engine itself (crossfade, video handling, volume
  indicator, pause) is **unchanged** — only which list of slides feeds it
  changes.

### 7. Selecting a show from the overlay

- `POST /api/local/pin-show` with the chosen show's id (or `null` for
  "Main"), then close the overlay. If using the shared state module from
  §4, update `pinned_show_id` locally right away for instant visual
  feedback rather than waiting for the next poll.
- Selecting "Settings…": close the overlay, then `router.push('/settings')`
  — the existing PIN-gated flow (`PinGate.vue`, `pinLock.js`) is untouched
  and fires normally since it's a real route entry.

## Explicitly out of scope / already decided

- **No per-device server-assigned show.** Don't add anything resembling
  `slide_announcers.show_id` — an earlier design sketch considered this and
  it was deliberately dropped in favor of full on-device sync + local pin.
- **No client-side language filtering.** The server already resolves each
  show's slide list fully language-curated; don't re-filter by
  `device.language_id` anywhere in the sync/playback path. That field is
  now purely a UI-chrome-language setting, if the frontend uses it for that
  at all.
- **Pin state is not reported back to the server.** There's no fleet-wide
  visibility into "which show is each kiosk currently showing" — this was a
  deliberate scope cut for v1, not an oversight. If it's wanted later, it'd
  need a new authenticated endpoint plus a schema field on `SlideAnnouncer`.

## Verification checklist

- [ ] `GET /api/slide-announcers/shows` returns the expected multi-show
      JSON for a paired test device (check against a real deployment/staging
      instance of the main app).
- [ ] Fresh sync populates per-show manifest/playlist files correctly for an
      entity with 2+ shows.
- [ ] Deleting a non-Main show server-side, then syncing, removes its cached
      media and (if it was pinned) reverts playback to Main.
- [ ] Pressing Menu opens the overlay **over** the current slideshow (video
      underneath keeps playing/paused sensibly, no route change, browser
      history unaffected).
- [ ] Arrow keys + Enter navigate and select overlay items; Back closes the
      overlay without changing the pin.
- [ ] Selecting a non-Main show pins it; restarting the local-app process
      (simulating a reboot) shows the pin survived.
- [ ] Selecting "Settings…" from the overlay reaches the existing PIN-gated
      settings flow unchanged.
- [ ] Unpairing wipes the pinned-show file (check `WIPE_PATHS` took effect).
- [ ] Legacy `GET /api/slide-announcers/slides` still works untouched for a
      kiosk build that hasn't been updated yet (backward-compat check).
