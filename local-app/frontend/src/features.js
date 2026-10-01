// On/off state for the optional features Settings > Advanced switches
// (Revelation Peering, LAN Video Receiver) — shared between Advanced.vue,
// which flips them, and SettingsLayout.vue, whose rail only lists a
// feature's own settings page while it's on. Same tiny reactive-module
// style as menuOverlay.js.
import { reactive } from 'vue'
import { api } from './api.js'

export const features = reactive({
  loaded: false,
  revelation: false,
  srtSink: false,
  // Admin dashboard's fleet-wide SRT force-disable (see srt_sink.py) —
  // shown as a warning next to the switch, doesn't change what it does.
  srtSinkServerAllows: true,
})

export async function loadFeatures() {
  const [revelation, srt] = await Promise.allSettled([api.revelationEnabled(), api.srtSinkStatus()])
  if (revelation.status === 'fulfilled') features.revelation = revelation.value.enabled
  if (srt.status === 'fulfilled') {
    features.srtSink = srt.value.local_enabled
    features.srtSinkServerAllows = srt.value.server_allows
  }
  features.loaded = true
}

export async function setFeature(name, enabled) {
  if (name === 'revelation') {
    features.revelation = (await api.setRevelationEnabled(enabled)).enabled
  } else if (name === 'srtSink') {
    const data = await api.setSrtSink(enabled)
    features.srtSink = data.local_enabled
    features.srtSinkServerAllows = data.server_allows
  }
}
