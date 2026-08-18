// Reactive open/closed state for the Menu overlay (see MenuOverlay.vue) —
// same tiny-module style as pinLock.js, but reactive since a component
// (MenuOverlay.vue) needs to react to it; pinLock.js's flag, by contrast,
// is only ever read imperatively from a router guard.
import { ref } from 'vue'

export const menuOpen = ref(false)

export function openMenu() {
  menuOpen.value = true
}

export function closeMenu() {
  menuOpen.value = false
}
