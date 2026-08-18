// Spatial keyboard navigation for the on-device kiosk remote. The remote's
// big front buttons send plain arrow keys + Enter, and it has two more
// buttons evscan reports as KEY_BACK and KEY_COMPOSE — there's no Tab key
// on it at all, so the browser's native Tab/Shift+Tab focus order (which
// every view already relies on via plain focusable <button>/<a>/<input>
// elements and visible :focus-visible rings, see style.css) is otherwise
// unreachable on this hardware.
//
// This installs one global keydown listener instead of per-view wiring:
//   - Arrow keys move focus to the nearest focusable element in that
//     direction. Works unchanged for both a single-column form (Pairing,
//     WifiConnect) and the two-column Settings rail+content layout, since
//     it's based on actual on-screen position, not DOM order. While the
//     Menu overlay is open, candidates are scoped to just its own list and
//     Up/Down wrap top-to-bottom/bottom-to-top (wrapCandidate below) — the
//     rest of the app deliberately doesn't wrap.
//   - Enter is deliberately left alone — a focused button/link/list item
//     already activates on Enter natively; Pairing.vue additionally wires
//     Enter on its own text fields to move to the next field, which this
//     module doesn't need to know about.
//   - The Back button navigates up one level via the router — unless the
//     Menu overlay (menuOverlay.js) is open, in which case Back just closes
//     it without touching route history.
//   - The Menu/Compose button opens the Menu overlay on top of whatever's
//     currently showing (unless already inside /settings, where Menu is a
//     no-op — Settings is reached *from* the overlay, not the reverse).
//
// Evdev key names Chromium/labwc report for Back/Compose depend on the
// XKB mapping in use and haven't been confirmed on real hardware yet —
// BACK_KEYS/MENU_KEYS below list every plausible candidate rather than
// guessing one; if testing on the device turns up a different string
// (check `event.key` via the Settings > Key Debug screen), add it to the
// relevant list.
//
// The remote's Home button is confirmed on real hardware (via Key Debug)
// to report event.key === 'BrowserHome' (keyCode 172).
import { menuOpen, openMenu, closeMenu } from './menuOverlay.js'

const DIRECTION_KEYS = { ArrowDown: 'down', ArrowUp: 'up', ArrowLeft: 'left', ArrowRight: 'right' }
const BACK_KEYS = ['GoBack', 'BrowserBack', 'Back', 'Escape']
const MENU_KEYS = ['ContextMenu', 'Menu', 'Compose', 'AppSwitch']
const HOME_KEYS = ['BrowserHome']

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

function focusableElements() {
  // While the Menu overlay is open, scope candidates to just its own
  // buttons — otherwise a press at the top/bottom edge of the (much
  // shorter) menu list could find its way to something in the slideshow
  // or settings page sitting underneath the scrim.
  const root = menuOpen.value ? document.querySelector('.menu-overlay') : document
  return Array.from((root || document).querySelectorAll(FOCUSABLE_SELECTOR))
    .filter((el) => el.offsetParent !== null) // skip display:none / hidden ancestors
}

function center(el) {
  const rect = el.getBoundingClientRect()
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
}

// Closest focusable element roughly in `direction` from `current`,
// preferring one directly ahead over one merely closer in a straight-line
// sense — weighting perpendicular drift keeps "down" from the Settings
// rail landing in the content pane just because it's a few pixels nearer.
function findNext(current, direction) {
  const from = center(current)
  let best = null
  let bestScore = Infinity

  for (const el of focusableElements()) {
    if (el === current) continue
    const to = center(el)
    const dx = to.x - from.x
    const dy = to.y - from.y

    let primary
    let perpendicular
    if (direction === 'down') { if (dy <= 0) continue; primary = dy; perpendicular = Math.abs(dx) }
    else if (direction === 'up') { if (dy >= 0) continue; primary = -dy; perpendicular = Math.abs(dx) }
    else if (direction === 'right') { if (dx <= 0) continue; primary = dx; perpendicular = Math.abs(dy) }
    else if (direction === 'left') { if (dx >= 0) continue; primary = -dx; perpendicular = Math.abs(dy) }

    const score = primary + perpendicular * 3
    if (score < bestScore) { bestScore = score; best = el }
  }

  return best
}

// Top/bottom-edge wrap, used only for the Menu overlay's vertical list
// (see findNext's caller below) — the rest of the app's spatial nav stays
// non-wrapping, since findNext() already returns null there and nothing
// calls this otherwise.
function wrapCandidate(direction) {
  const elements = focusableElements()
  if (!elements.length) return null
  const byY = [...elements].sort((a, b) => center(a).y - center(b).y)
  return direction === 'down' ? byY[0] : byY[byY.length - 1]
}

function isTypingTarget(el) {
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)
}

export function installRemoteNav(router) {
  // Give every new page something focused so the first arrow-key press
  // has a starting point, without stealing focus from an <input autofocus>
  // a view already set (Pairing's code field, WifiConnect's password
  // field). Inside Settings, prefer the rail item for the section just
  // navigated to, so Up/Down immediately walks the rail instead of
  // requiring a jump into content first.
  router.afterEach(() => {
    requestAnimationFrame(() => {
      if (isTypingTarget(document.activeElement)) return
      const activeRailItem = document.querySelector('.rail-item--active')
      const target = activeRailItem || focusableElements()[0]
      if (target) target.focus()
    })
  })

  window.addEventListener('keydown', (event) => {
    const direction = DIRECTION_KEYS[event.key]
    if (direction) {
      // Inside a text input (or a <select>), Left/Right should move the
      // text cursor / change the selected value like on any normal
      // keyboard. But every field here is single-line, so Up/Down has no
      // native meaning to preserve — without a Tab key on this remote,
      // leaving Up/Down captured by the input would trap focus there with
      // no way out but Enter. So Up/Down always falls through to spatial
      // navigation; only Left/Right defer to native text-cursor movement.
      const active = document.activeElement
      const blockedForTyping = (direction === 'left' || direction === 'right') &&
        (isTypingTarget(active) || active?.tagName === 'SELECT')
      if (blockedForTyping) return

      let next = findNext(active || document.body, direction)
      if (!next && menuOpen.value && (direction === 'up' || direction === 'down')) {
        next = wrapCandidate(direction)
      }
      if (next) {
        event.preventDefault()
        next.focus()
      }
      return
    }

    if (HOME_KEYS.includes(event.key)) {
      event.preventDefault()
      router.push('/kiosk')
      return
    }

    if (BACK_KEYS.includes(event.key)) {
      event.preventDefault()
      if (menuOpen.value) {
        // Dismiss without falling through to router.back() — the overlay
        // isn't a route, so there's no history entry to undo, and closing
        // it must never change the pin.
        closeMenu()
        return
      }
      if (window.history.state?.back) router.back()
      else router.push('/kiosk')
      return
    }

    if (MENU_KEYS.includes(event.key)) {
      event.preventDefault()
      if (menuOpen.value || router.currentRoute.value.path.startsWith('/settings')) return
      openMenu()
    }
  })
}
