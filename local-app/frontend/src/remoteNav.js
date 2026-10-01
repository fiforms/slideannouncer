// Keyboard navigation for the on-device kiosk remote. The remote's big
// front buttons send plain arrow keys + Enter, and it has two more buttons
// evscan reports as KEY_BACK and KEY_COMPOSE — there's no Tab key on it at
// all, so the browser's native Tab/Shift+Tab focus order is unreachable on
// this hardware. This one global keydown listener (plus a focusin listener
// and a MutationObserver) replaces it everywhere.
//
// Settings is split into two zones, smart-TV style (SettingsLayout.vue
// marks them with data-nav-zone="rail" / "content"):
//   - Rail zone: Up/Down walks the category list, and each step swaps the
//     content pane to that category (a short debounce, BROWSE_DELAY_MS,
//     keeps a fast scroll past a category from mounting it and firing its
//     on-load fetches/scans). Enter (or Right) moves into the content pane.
//     Back leaves Settings for the slideshow.
//   - Content zone: arrows move only among the content pane's own
//     controls. Back goes up one level for a sub-page (each route's
//     meta.parent in router.js — e.g. a WiFi network → the WiFi list),
//     otherwise returns to the rail. Left with nothing further left also
//     returns to the rail.
// navZone below is exported so SettingsLayout can tint whichever zone is
// active.
//
// Arrow movement anywhere is spatial — nearest control in that direction
// by on-screen edges (see findNext), not DOM order — so rows of side-by-
// side buttons and single-column forms both behave predictably.
//
// Text fields and sliders keep Left/Right for themselves (caret movement /
// value change) until the caret or value is already at that edge; one
// more fresh press (not a key auto-repeat, so holding Left to get to the
// start of a field can't overshoot out of it) then moves to the next
// control that way — e.g. password field → its Show button. Up/Down always
// navigate, since every field here is single-line.
//
// Modals (an open <dialog>, or any element marked data-nav-modal — the QR
// lightboxes) scope arrow navigation to themselves and take focus when
// they open (their [autofocus] control, if any); Back clicks their
// [data-nav-close] button, or failing that fires a `navclose` event on the
// modal for it to close itself; focus goes back to where it was once they
// close. data-nav-wrap makes Up/Down wrap top↔bottom inside one (Dropdown's
// option list). The Menu overlay is handled the same way, and always wraps.
//
// Focus is also kept from silently disappearing: a focused button that
// goes disabled mid-action (Check for Update while checking) gets focus
// back once it re-enables, and one removed from the page entirely hands
// focus to whatever control is now closest to where it was.
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
//
// The first-run setup wizard (/setup) is a plain full-page route — no
// zones — but Back follows each step's meta.parent like a Settings sub-
// page, and Home/Menu do nothing there, so the remote can't drop someone
// out of setup halfway through.
import { ref } from 'vue'
import { menuOpen, openMenu, closeMenu } from './menuOverlay.js'

const DIRECTION_KEYS = { ArrowDown: 'down', ArrowUp: 'up', ArrowLeft: 'left', ArrowRight: 'right' }
const BACK_KEYS = ['GoBack', 'BrowserBack', 'Back', 'Escape']
const MENU_KEYS = ['ContextMenu', 'Menu', 'Compose', 'AppSwitch']
const HOME_KEYS = ['BrowserHome']

const BROWSE_DELAY_MS = 200
// How long a route change waits for its new page's controls to show up
// (most settings pages render their buttons only after a fetch) before
// giving up on the preferred target and taking whatever is there.
const PENDING_FOCUS_TIMEOUT_MS = 3000

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"]):not([disabled])',
].join(', ')

// 'rail' | 'content' — which half of Settings has focus. Only meaningful
// while a Settings route is showing.
export const navZone = ref('rail')

let router = null
let lastFocused = null
let lastRect = null
let lastContentFocus = null
let focusBeforeModal = null
let pendingFocus = null
let pendingFocusTimer = null
let browseTimer = null
let browsePath = null
let navIntent = null
let syncScheduled = false
const savedContentIndex = new Map()

// ---------------------------------------------------------------- queries

function isVisible(el) {
  return el.getClientRects().length > 0
}

function isFocusable(el) {
  return !!el && el.isConnected && el.matches(FOCUSABLE_SELECTOR) && isVisible(el)
}

function focusablesIn(root) {
  return Array.from((root || document).querySelectorAll(FOCUSABLE_SELECTOR)).filter(isVisible)
}

function zoneRoot(zone) {
  return document.querySelector(`[data-nav-zone="${zone}"]`)
}

function activeModal() {
  if (menuOpen.value) return document.querySelector('.menu-overlay')
  const modals = document.querySelectorAll('dialog[open], [data-nav-modal]')
  return modals[modals.length - 1] || null
}

function inSettings() {
  return router.currentRoute.value.path.startsWith('/settings')
}

function inSetup() {
  return router.currentRoute.value.path.startsWith('/setup')
}

// Where arrow keys may move right now: an open modal, else the active
// Settings zone, else the whole page.
function scopeRoot() {
  return activeModal() || (inSettings() && zoneRoot(navZone.value)) || document
}

function currentRailItem() {
  const railPath = router.currentRoute.value.meta.railPath
  return document.querySelector(`[data-rail-path="${railPath}"]`)
}

function preferredIn(root) {
  const auto = root.querySelector('[autofocus]')
  if (auto && isFocusable(auto)) return auto
  return focusablesIn(root)[0] || null
}

function focusEl(el) {
  if (!el) return false
  el.focus()
  return document.activeElement === el
}

// ------------------------------------------------------- spatial movement

// Gap between two 1-D ranges (0 when they overlap).
function rangeGap(aStart, aEnd, bStart, bEnd) {
  return Math.max(0, bStart - aEnd, aStart - bEnd)
}

// Nearest focusable in `direction` from rect `from`, among `candidates`.
// Edge-based rather than center-based: a candidate only counts as "below"
// if it starts below the current control's bottom edge, so a taller or
// offset neighbour in the same row can never be mistaken for the next row
// down. Among those, how far the candidate is off to the side (measured
// from the current control's center to the candidate's span — 0 when the
// candidate sits directly in line) is weighted heavily, so Down from the
// right-hand button of a row lands on the right-hand button of the row
// below rather than whichever is a few pixels closer.
function findNext(from, direction, candidates, exclude) {
  const vertical = direction === 'up' || direction === 'down'
  const forward = direction === 'down' || direction === 'right'
  const fromCenterX = from.left + from.width / 2
  const fromCenterY = from.top + from.height / 2

  function score(to, strict) {
    const toCenterX = to.left + to.width / 2
    const toCenterY = to.top + to.height / 2
    let primary
    if (strict) {
      // 2px of slack for borders/outlines that overlap by a hair
      primary = vertical
        ? (forward ? to.top - from.bottom : from.top - to.bottom)
        : (forward ? to.left - from.right : from.left - to.right)
      if (primary < -2) return null
      primary = Math.max(0, primary)
    } else {
      primary = vertical
        ? (forward ? toCenterY - fromCenterY : fromCenterY - toCenterY)
        : (forward ? toCenterX - fromCenterX : fromCenterX - toCenterX)
      if (primary <= 0) return null
    }
    const offAxis = vertical
      ? rangeGap(fromCenterX, fromCenterX, to.left, to.right)
      : rangeGap(fromCenterY, fromCenterY, to.top, to.bottom)
    const centerDrift = vertical ? Math.abs(toCenterX - fromCenterX) : Math.abs(toCenterY - fromCenterY)
    return primary + offAxis * 3 + centerDrift * 0.05
  }

  // Strict pass first; the center-based pass only catches layouts where
  // nothing lies fully beyond the current edge (e.g. a tall control
  // beside short ones).
  for (const strict of [true, false]) {
    let best = null
    let bestScore = Infinity
    for (const el of candidates) {
      if (el === exclude) continue
      const s = score(el.getBoundingClientRect(), strict)
      if (s !== null && s < bestScore) { bestScore = s; best = el }
    }
    if (best) return best
  }
  return null
}

// Top/bottom-edge wrap, used only for the Menu overlay's vertical list —
// the rest of the app's spatial nav deliberately doesn't wrap.
function wrapCandidate(direction, candidates) {
  if (!candidates.length) return null
  const byY = [...candidates].sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)
  return direction === 'down' ? byY[0] : byY[byY.length - 1]
}

// ------------------------------------------------- text fields & sliders

function isTextField(el) {
  if (!el) return false
  if (el.tagName === 'TEXTAREA' || el.isContentEditable) return true
  return el.tagName === 'INPUT' && !['button', 'submit', 'reset', 'checkbox', 'radio', 'range'].includes(el.type)
}

function isSlider(el) {
  return el?.tagName === 'INPUT' && el.type === 'range'
}

// True when a Left/Right press can't do anything more inside the field
// itself, so it should move focus instead.
function atEdge(el, direction) {
  if (isSlider(el)) {
    const value = Number(el.value)
    return direction === 'left' ? value <= Number(el.min || 0) : value >= Number(el.max || 100)
  }
  let start
  let end
  try {
    start = el.selectionStart
    end = el.selectionEnd
  } catch {
    return true
  }
  // Input types without a selection API (email, number…) report null —
  // nothing to protect, so treat them as always at the edge.
  if (start == null) return true
  if (start !== end) return false
  return direction === 'left' ? start === 0 : start === (el.value ?? '').length
}

// --------------------------------------------------------------- zones

function enterContent() {
  // Enter pressed on a rail item before its browse debounce fired — show
  // that category first, then focus into it once it has rendered.
  if (browseTimer) {
    const path = browsePath
    cancelBrowse()
    navIntent = 'content'
    router.replace(path)
    return
  }
  const root = zoneRoot('content')
  if (!root) return
  // Re-entering the same page goes back to the control you left from.
  if (lastContentFocus && root.contains(lastContentFocus) && focusEl(lastContentFocus)) return
  if (focusEl(preferredIn(root))) return
  requestFocus({ zone: 'content' })
}

function backToRail() {
  focusEl(currentRailItem())
}

function scheduleBrowse(path) {
  cancelBrowse()
  if (path === router.currentRoute.value.meta.railPath) return
  browsePath = path
  browseTimer = setTimeout(() => {
    browseTimer = null
    navIntent = 'rail'
    router.replace(path)
  }, BROWSE_DELAY_MS)
}

function cancelBrowse() {
  if (browseTimer) {
    clearTimeout(browseTimer)
    browseTimer = null
  }
}

function goUp(parent) {
  navIntent = 'up'
  if (window.history.state?.back === parent) router.back()
  else router.replace(parent)
}

// ---------------------------------------------------- focus maintenance

// Ask for focus to land somewhere once the next page has rendered its
// controls — a route change's new view mounts after afterEach, and most
// settings pages only render their buttons after a fetch.
function requestFocus(target) {
  pendingFocus = { ...target, deadline: Date.now() + PENDING_FOCUS_TIMEOUT_MS }
  clearTimeout(pendingFocusTimer)
  pendingFocusTimer = setTimeout(scheduleSync, PENDING_FOCUS_TIMEOUT_MS + 50)
  scheduleSync()
}

function tryPendingFocus(timedOut) {
  const { zone, index } = pendingFocus
  if (zone === 'page') {
    if (isFocusable(document.activeElement)) return true
    return focusEl(preferredIn(document)) || timedOut
  }
  if (zone === 'rail') {
    // Already somewhere on the rail (e.g. pressed Down again while the
    // last browse was still loading) — don't yank it back.
    if (zoneRoot('rail')?.contains(document.activeElement)) return true
    return focusEl(currentRailItem()) || timedOut
  }
  const root = zoneRoot('content')
  if (!root) return timedOut
  // Restoring a remembered position: wait (up to the deadline) for the
  // list to grow back to it, then settle for the page's first control.
  if (index != null && !timedOut) {
    const list = focusablesIn(root)
    return list.length > index && focusEl(list[index])
  }
  // If the content pane has nothing focusable yet (WiFi list mid-scan with
  // Rescan disabled, Connect form swapped for "Connecting…"), this stays
  // pending — however long that takes — rather than falling back to the
  // rail, which would yank you out of the page you're working in. focusin
  // below drops the request if you leave the pane yourself.
  return focusEl(preferredIn(root))
}

function recoverFocus() {
  if (!lastFocused) return
  if (isFocusable(lastFocused)) {
    lastFocused.focus()
    return
  }
  // Still on the page, just disabled for now — wait for it to come back
  // rather than yanking focus somewhere unrelated mid-action.
  if (lastFocused.isConnected && isVisible(lastFocused)) return
  const candidates = focusablesIn(scopeRoot())
  if (!candidates.length || !lastRect) {
    // The pane has nothing to focus right now — wait in it for the next
    // control to appear rather than jumping to the other zone.
    if (inSettings()) requestFocus({ zone: navZone.value })
    return
  }
  const cx = lastRect.left + lastRect.width / 2
  const cy = lastRect.top + lastRect.height / 2
  let best = null
  let bestDist = Infinity
  for (const el of candidates) {
    const r = el.getBoundingClientRect()
    const d = Math.hypot(r.left + r.width / 2 - cx, r.top + r.height / 2 - cy)
    if (d < bestDist) { bestDist = d; best = el }
  }
  focusEl(best)
}

function syncFocus() {
  syncScheduled = false
  const active = document.activeElement
  const modal = activeModal()

  if (modal) {
    if (!modal.contains(active)) {
      if (!focusBeforeModal) focusBeforeModal = lastFocused
      focusEl(preferredIn(modal))
    }
    return
  }

  if (pendingFocus) {
    focusBeforeModal = null
    if (tryPendingFocus(Date.now() >= pendingFocus.deadline)) {
      pendingFocus = null
      clearTimeout(pendingFocusTimer)
    }
    return
  }

  if (focusBeforeModal) {
    const el = focusBeforeModal
    focusBeforeModal = null
    if (isFocusable(el)) {
      el.focus()
      return
    }
    // Still there, just disabled for a moment (a Dropdown whose choice is
    // saving) — make it the control recoverFocus() waits on, so focus
    // comes back to it once it re-enables instead of going to whatever
    // else is nearest the closed modal.
    if (el.isConnected && isVisible(el)) {
      lastFocused = el
      lastRect = el.getBoundingClientRect()
    }
  }

  const hasFocus = active && active !== document.body && isFocusable(active)
  if (!hasFocus && inSettings()) recoverFocus()
}

function scheduleSync() {
  if (syncScheduled) return
  syncScheduled = true
  requestAnimationFrame(syncFocus)
}

// ------------------------------------------------------------- install

export function installRemoteNav(appRouter) {
  router = appRouter

  router.beforeEach((to, from) => {
    cancelBrowse()
    // Remember which control was focused on a page you're leaving from the
    // content pane, so coming back up to it (Back from a sub-page) lands
    // there again rather than at the top.
    if (from.path.startsWith('/settings') && navZone.value === 'content') {
      const root = zoneRoot('content')
      const index = root ? focusablesIn(root).indexOf(document.activeElement) : -1
      if (index >= 0) savedContentIndex.set(from.path, index)
    }
  })

  router.afterEach((to, from, failure) => {
    const intent = navIntent
    navIntent = null
    if (failure) return
    if (!to.path.startsWith('/settings')) requestFocus({ zone: 'page' })
    else if (!from.path.startsWith('/settings') || intent === 'rail') requestFocus({ zone: 'rail' })
    else if (intent === 'content') requestFocus({ zone: 'content' })
    else if (intent === 'up') requestFocus({ zone: 'content', index: savedContentIndex.get(to.path) })
    else requestFocus({ zone: navZone.value })
  })

  document.addEventListener('focusin', (event) => {
    const el = event.target
    lastFocused = el
    lastRect = el.getBoundingClientRect()
    const zone = el.closest?.('[data-nav-zone]')?.dataset.navZone
    if (!zone) return
    // Focus moved to the other pane (Back/Left to the rail, a click) — a
    // pending "focus this pane once it has controls" request is stale.
    if (pendingFocus && pendingFocus.zone !== 'page' && pendingFocus.zone !== zone) {
      pendingFocus = null
      clearTimeout(pendingFocusTimer)
    }
    navZone.value = zone
    if (zone === 'content') {
      lastContentFocus = el
      cancelBrowse()
    } else if (el.dataset.railPath) {
      scheduleBrowse(el.dataset.railPath)
    } else {
      cancelBrowse()
    }
  })

  new MutationObserver(scheduleSync).observe(document.body, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['disabled', 'open', 'data-nav-modal'],
  })

  window.addEventListener('keydown', (event) => {
    const active = document.activeElement
    const hasFocus = active && active !== document.body && active.isConnected
    const settingsZone = inSettings() && !activeModal() ? navZone.value : null
    const direction = DIRECTION_KEYS[event.key]

    if (direction) {
      if ((direction === 'left' || direction === 'right') && hasFocus && (isTextField(active) || isSlider(active))) {
        if (!atEdge(active, direction) || event.repeat) return
      }

      if (settingsZone === 'rail' && direction === 'right' && hasFocus) {
        event.preventDefault()
        enterContent()
        return
      }

      const root = scopeRoot()
      const candidates = focusablesIn(root)
      // Focus lost to a disabled/removed control — navigate from where it was.
      const from = hasFocus ? active.getBoundingClientRect() : lastRect
      let next = from ? findNext(from, direction, candidates, active) : candidates[0]
      const wraps = menuOpen.value || activeModal()?.hasAttribute('data-nav-wrap')
      if (!next && wraps && (direction === 'up' || direction === 'down')) {
        next = wrapCandidate(direction, candidates)
      }
      if (next) {
        event.preventDefault()
        next.focus()
        return
      }
      if (settingsZone === 'content') {
        event.preventDefault()
        if (direction === 'left') backToRail()
        // Nothing more to focus that way, but there may be more text to
        // read below/above the last control.
        else if (direction === 'down' || direction === 'up') {
          root.scrollBy({ top: (direction === 'down' ? 1 : -1) * root.clientHeight * 0.4, behavior: 'smooth' })
        }
      } else if (settingsZone === 'rail') {
        event.preventDefault()
      }
      return
    }

    if (event.key === 'Enter' && settingsZone === 'rail' && active?.dataset.railPath) {
      event.preventDefault()
      enterContent()
      return
    }

    // While already on /kiosk with the Menu overlay closed, Home/Back are
    // owned by Slideshow.vue's own listener (RESTART_KEYS) — they restart
    // the show in place rather than navigating, so this global handler must
    // not also push/back the route out from under it.
    const onKioskWithoutMenu = router.currentRoute.value.path === '/kiosk' && !menuOpen.value

    if (HOME_KEYS.includes(event.key)) {
      event.preventDefault()
      if (onKioskWithoutMenu || inSetup()) return
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
      const modal = activeModal()
      if (modal) {
        const close = modal.querySelector('[data-nav-close]')
        if (close) close.click()
        else if (modal.tagName === 'DIALOG') modal.close()
        // No close button to click (a Dropdown's open list) — let the
        // component close itself.
        else modal.dispatchEvent(new CustomEvent('navclose'))
        return
      }
      if (settingsZone === 'content') {
        const parent = router.currentRoute.value.meta.parent
        if (parent) goUp(parent)
        else backToRail()
        return
      }
      if (settingsZone === 'rail') {
        router.push('/kiosk')
        return
      }
      if (inSetup()) {
        const parent = router.currentRoute.value.meta.parent
        if (parent) goUp(parent)
        return
      }
      if (onKioskWithoutMenu) return
      if (window.history.state?.back) router.back()
      else router.push('/kiosk')
      return
    }

    if (MENU_KEYS.includes(event.key)) {
      event.preventDefault()
      if (menuOpen.value || inSettings() || inSetup()) return
      openMenu()
    }
  })
}
