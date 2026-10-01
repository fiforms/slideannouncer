// Whether the current in-memory session has already cleared the Settings
// PIN gate. Deliberately just a module-level flag, not anything persisted —
// this is a low-effort deterrent against someone grabbing the remote and
// poking at settings, not real access control (see SLIDE_ANNOUNCER.md,
// "Kiosk display" > Settings PIN). Resets to locked on every app reload and
// every time the router leaves the /settings section back to the kiosk.
//
// One exception: signing in to a captive portal (captivePortal.js) has to
// navigate the kiosk tab off to the portal's own site and back, which
// reloads the app — grantReloadPass() lets exactly that one next load
// skip the PIN, via sessionStorage (same tab only, consumed on read).
const RELOAD_PASS_KEY = 'pinLock.reloadPass'
let unlocked = consumeReloadPass()

function consumeReloadPass() {
  try {
    const granted = sessionStorage.getItem(RELOAD_PASS_KEY) === '1'
    sessionStorage.removeItem(RELOAD_PASS_KEY)
    return granted
  } catch {
    return false
  }
}

export function grantReloadPass() {
  try {
    sessionStorage.setItem(RELOAD_PASS_KEY, '1')
  } catch {
    // no storage — the PIN just gets asked again on the way back
  }
}

export function isUnlocked() {
  return unlocked
}

export function unlock() {
  unlocked = true
}

export function lock() {
  unlocked = false
}
