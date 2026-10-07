// The slideannouncer product's frontend: what it plugs into the core shell
// (docs/PRODUCTS.md, "Frontend seams"). Selected at build time by vite.config.js's
// `@product` alias.
import { api } from './api.js'
import { features, loadFeatures } from './features.js'
import Slideshow from './views/Slideshow.vue'
import MenuOverlay from './views/MenuOverlay.vue'
import SrtSink from './views/settings/SrtSink.vue'
import RevelationPeering from './views/settings/RevelationPeering.vue'
import AdvancedFeatures from './views/settings/AdvancedFeatures.vue'

export default {
  name: 'slideannouncer',
  // Mounted at /kiosk — the screen the device boots to.
  mainView: Slideshow,
  // Drawn over every route, opened by the Menu key.
  overlay: MenuOverlay,
  // Children of /settings, beyond the core ones.
  settingsRoutes: [
    { path: 'srt-sink', component: SrtSink, meta: { railPath: '/settings/srt-sink' } },
    { path: 'revelation', component: RevelationPeering, meta: { railPath: '/settings/revelation' } },
  ],
  // Extra rail entries (after Advanced); `t` is vue-i18n's translate.
  railCategories: (t) => [
    features.revelation && { path: '/settings/revelation', label: t('settingsLayout.revelationPeering'), sub: true },
    features.srtSink && { path: '/settings/srt-sink', label: t('settingsLayout.videoReceiver'), sub: true },
  ],
  // Rendered at the bottom of Settings > Advanced.
  advancedSection: AdvancedFeatures,
  // Run whenever Settings opens, to refresh whatever railCategories reads.
  loadSettings: loadFeatures,
  // The Settings PIN (null = none), for the PIN gate. Any failure throws.
  settingsPin: async () => (await api.slideshow()).settings?.settings_pin || null,
}
