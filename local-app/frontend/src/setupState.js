// Whether the first-run setup wizard (views/setup/) still needs to run —
// the backend's /api/local/status `setup_complete` (pairing.py's
// is_setup_complete()), seeded by main.js's boot-time status fetch and
// refreshed by Slideshow.vue's status poll (covering a boot where the
// backend wasn't answering yet). Read imperatively by router.js's guard,
// same tiny-module style as pinLock.js.
let setupRequired = false

export function isSetupRequired() {
  return setupRequired
}

export function setSetupRequired(required) {
  setupRequired = required
}
