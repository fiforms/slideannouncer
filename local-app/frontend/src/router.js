import { createRouter, createWebHistory } from 'vue-router'
import Slideshow from './views/Slideshow.vue'
import PinGate from './views/PinGate.vue'
import SettingsLayout from './views/settings/SettingsLayout.vue'
import NetworkStatus from './views/settings/NetworkStatus.vue'
import WifiList from './views/settings/WifiList.vue'
import WifiConnect from './views/settings/WifiConnect.vue'
import System from './views/settings/System.vue'
import Advanced from './views/settings/Advanced.vue'
import SrtSink from './views/settings/SrtSink.vue'
import RevelationPeering from './views/settings/RevelationPeering.vue'
import Pairing from './views/settings/Pairing.vue'
import DeviceTools from './views/settings/DeviceTools.vue'
import KeyDebug from './views/settings/KeyDebug.vue'
import SetupLayout from './views/setup/SetupLayout.vue'
import SetupWelcome from './views/setup/SetupWelcome.vue'
import SetupNetwork from './views/setup/SetupNetwork.vue'
import SetupName from './views/setup/SetupName.vue'
import SetupPairing from './views/setup/SetupPairing.vue'
import SetupDone from './views/setup/SetupDone.vue'
import { api } from './api.js'
import { isUnlocked, lock, unlock } from './pinLock.js'
import { isSetupRequired } from './setupState.js'

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
      // meta.railPath: which rail category stays highlighted for this
      // screen (sub-pages highlight their parent category). meta.parent:
      // where the remote's Back goes from this screen's content pane —
      // see remoteNav.js; screens without one return focus to the rail.
      children: [
        { path: '', redirect: '/settings/network' },
        { path: 'system', component: System, meta: { railPath: '/settings/system' } },
        { path: 'advanced', component: Advanced, meta: { railPath: '/settings/advanced' } },
        // Screen resolution now lives on Advanced.
        { path: 'screens', redirect: '/settings/advanced' },
        // meta.networkBase: these three screens are shared with the setup
        // wizard below, and link among themselves relative to it.
        { path: 'network', component: NetworkStatus, meta: { railPath: '/settings/network', networkBase: '/settings/network' } },
        { path: 'network/wifi', component: WifiList, meta: { railPath: '/settings/network', parent: '/settings/network', networkBase: '/settings/network' } },
        {
          path: 'network/wifi/:ssid',
          component: WifiConnect,
          props: true,
          meta: { railPath: '/settings/network', parent: '/settings/network/wifi', networkBase: '/settings/network' },
        },
        { path: 'srt-sink', component: SrtSink, meta: { railPath: '/settings/srt-sink' } },
        { path: 'revelation', component: RevelationPeering, meta: { railPath: '/settings/revelation' } },
        // Pairing lives here (not a standalone top-level route) so an
        // unpaired device's pairing form gets the same rail chrome as
        // every other settings screen.
        { path: 'pairing', component: Pairing, meta: { railPath: '/settings/pairing' } },
        // Neither of these is in the rail (SettingsLayout's `categories`) —
        // reached via the "Device Restart, Reset & Debugging" button on the
        // System page, and Key Debugging's own button on that page in turn.
        { path: 'device-tools', component: DeviceTools, meta: { railPath: '/settings/system', parent: '/settings/system' } },
        { path: 'keydebug', component: KeyDebug, meta: { railPath: '/settings/system', parent: '/settings/device-tools' } },
      ],
    },
    // First-run setup wizard — launched instead of the slideshow until
    // the backend reports setup_complete (see the guard below and
    // setupState.js). Full-page, not under /settings: no rail, and no PIN
    // gate. meta.step: which step the progress bar highlights. meta.parent:
    // where the remote's Back goes (remoteNav.js), same as in Settings.
    {
      path: '/setup',
      component: SetupLayout,
      children: [
        { path: '', component: SetupWelcome, meta: { step: 'welcome' } },
        { path: 'network', component: SetupNetwork, meta: { step: 'network', parent: '/setup', networkBase: '/setup/network' } },
        { path: 'network/wifi', component: WifiList, meta: { step: 'network', parent: '/setup/network', networkBase: '/setup/network' } },
        {
          path: 'network/wifi/:ssid',
          component: WifiConnect,
          props: true,
          meta: { step: 'network', parent: '/setup/network/wifi', networkBase: '/setup/network' },
        },
        { path: 'name', component: SetupName, meta: { step: 'name', parent: '/setup/network' } },
        { path: 'pairing', component: SetupPairing, meta: { step: 'pairing', parent: '/setup/name' } },
        { path: 'done', component: SetupDone, meta: { step: 'done', parent: '/setup/pairing' } },
      ],
    },
  ],
})

// A fresh (or factory-reset) device lands in the setup wizard instead of
// the slideshow. Only /kiosk is redirected — everything else that leaves
// the wizard (Settings' "Back to slideshow", the remote's Home) goes
// through /kiosk anyway.
router.beforeEach((to) => {
  if (to.path === '/kiosk' && isSetupRequired()) return '/setup'
  return true
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
