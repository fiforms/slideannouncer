import { createApp } from 'vue'
import App from './App.vue'
import router from './router.js'
import { installRemoteNav } from './remoteNav.js'
import { i18n, setLocale } from './i18n.js'
import { api } from './api.js'
import './style.css'

installRemoteNav(router)

// Resolve the device's effective language (pairing.read_effective_language()
// on the backend: server-assigned once paired, else the boot-yaml hint)
// before the very first render, so there's no flash of English on an
// already-configured device. A fetch failure just leaves the 'en' default
// i18n.js starts with — Slideshow.vue's own recurring status poll will
// pick up the real language shortly after mount regardless.
async function bootstrap() {
  try {
    const status = await api.localStatus()
    setLocale(status.language)
  } catch {
    // stay on the default locale
  }
  createApp(App).use(router).use(i18n).mount('#app')
}

bootstrap()
