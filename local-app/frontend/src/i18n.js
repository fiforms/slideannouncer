import { createI18n } from 'vue-i18n'
import en from './locales/en.json'
import es from './locales/es.json'

// Only languages this app actually ships translations for — a device
// assigned a language the fleet doesn't have strings for yet (e.g. a new
// row in the server's `languages` table with no matching locale file here)
// falls back to English rather than showing an empty/undefined locale.
export const SUPPORTED_LOCALES = ['en', 'es']

export const i18n = createI18n({
  legacy: false,
  locale: 'en',
  fallbackLocale: 'en',
  messages: { en, es },
})

// Called at boot with the device's effective language (see
// pairing.read_effective_language(), surfaced as `language` on
// GET /api/local/status) and again every time Slideshow.vue's status poll
// picks up a change — e.g. an entity admin assigning/changing this
// device's language reaches it within one heartbeat interval, same as
// device_name/entity_name already do.
export function setLocale(code) {
  i18n.global.locale.value = SUPPORTED_LOCALES.includes(code) ? code : 'en'
}
