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
//     it's based on actual on-screen position, not DOM order.
//   - Enter is deliberately left alone — a focused button/link/list item
//     already activates on Enter natively; Pairing.vue additionally wires
//     Enter on its own text fields to move to the next field, which this
//     module doesn't need to know about.
//   - The Back button navigates up one level via the router.
//   - The Menu/Compose button jumps straight to Settings from anywhere,
//     standing in for an on-screen menu affordance this kiosk doesn't have.
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
  return Array.from(document.querySelectorAll(FOCUSABLE_SELECTOR))
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
      // Inside a text input (or a <select>), arrow keys should move the
      // text cursor / change the selected value like on any normal
      // keyboard — only hijack them when focus is on a button/link/item.
      const active = document.activeElement
      if (isTypingTarget(active) || active?.tagName === 'SELECT') return

      const next = findNext(active || document.body, direction)
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
      if (window.history.state?.back) router.back()
      else router.push('/kiosk')
      return
    }

    if (MENU_KEYS.includes(event.key)) {
      event.preventDefault()
      router.push('/settings')
    }
  })
}
