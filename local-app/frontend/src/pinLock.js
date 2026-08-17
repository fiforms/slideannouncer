// Whether the current in-memory session has already cleared the Settings
// PIN gate. Deliberately just a module-level flag, not anything persisted —
// this is a low-effort deterrent against someone grabbing the remote and
// poking at settings, not real access control (see SLIDE_ANNOUNCER.md,
// "Kiosk display" > Settings PIN). Resets to locked on every app reload and
// every time the router leaves the /settings section back to the kiosk.
let unlocked = false

export function isUnlocked() {
  return unlocked
}

export function unlock() {
  unlocked = true
}

export function lock() {
  unlocked = false
}
