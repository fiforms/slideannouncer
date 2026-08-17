import { createRouter, createWebHistory } from 'vue-router'
import Slideshow from './views/Slideshow.vue'
import PinGate from './views/PinGate.vue'
import SettingsLayout from './views/settings/SettingsLayout.vue'
import NetworkStatus from './views/settings/NetworkStatus.vue'
import WifiList from './views/settings/WifiList.vue'
import WifiConnect from './views/settings/WifiConnect.vue'
import System from './views/settings/System.vue'
import Pairing from './views/settings/Pairing.vue'
import DeviceTools from './views/settings/DeviceTools.vue'
import KeyDebug from './views/settings/KeyDebug.vue'
import { api } from './api.js'
import { isUnlocked, lock, unlock } from './pinLock.js'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    // Home.vue (device status + links to Pairing/Settings) was redundant
    // with Settings > System, which already surfaces the same status;
    // '/' now just lands on the slideshow directly.
    { path: '/', redirect: '/kiosk' },
    { path: '/kiosk', component: Slideshow },
    // Top-level, not nested under /settings — it must render without the
    // rail/chrome SettingsLayout gives every real settings screen, since
    // it's a lock screen, not a settings section of its own.
    { path: '/pin-lock', component: PinGate },
    {
      path: '/settings',
      component: SettingsLayout,
      children: [
        { path: '', redirect: '/settings/system' },
        { path: 'system', component: System },
        { path: 'network', component: NetworkStatus },
        { path: 'network/wifi', component: WifiList },
        { path: 'network/wifi/:ssid', component: WifiConnect, props: true },
        // Pairing lives here (not a standalone top-level route) so an
        // unpaired device's pairing form gets the same rail chrome as
        // every other settings screen.
        { path: 'pairing', component: Pairing },
        // Neither of these is in the rail (SettingsLayout's `categories`) —
        // reached via the "Device Restart, Reset & Debugging" button on the
        // System page, and Key Debugging's own button on that page in turn.
        { path: 'device-tools', component: DeviceTools },
        { path: 'keydebug', component: KeyDebug },
      ],
    },
  ],
})

// Settings PIN gate — see PinGate.vue and pinLock.js. Only checked on the
// way *into* /settings from outside it, and only while this session hasn't
// already cleared the gate, so navigating around within Settings never
// re-fetches or re-prompts. Leaving /settings back out to the kiosk relocks
// it, so the PIN is required again next time someone opens Settings.
router.beforeEach(async (to, from) => {
  const enteringSettings = to.path.startsWith('/settings')

  if (from.path.startsWith('/settings') && !enteringSettings) lock()

  if (!enteringSettings || isUnlocked()) return true

  let pin = null
  try {
    const data = await api.slideshow()
    pin = data.settings?.settings_pin || null
  } catch {
    // Can't reach the local backend — fail open rather than stranding
    // someone out of Settings entirely over a fetch hiccup.
  }

  if (!pin) {
    // No PIN configured — nothing to gate. Mark unlocked so subsequent
    // in-settings navigation doesn't refetch this on every click.
    unlock()
    return true
  }

  return { path: '/pin-lock', query: { redirect: to.fullPath } }
})

export default router
