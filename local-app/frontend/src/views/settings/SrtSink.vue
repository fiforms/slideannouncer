<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import QRCode from 'qrcode'
import { api } from '../../api.js'
import ToggleSwitch from '../../components/ToggleSwitch.vue'

const { t } = useI18n()

const localEnabled = ref(false)
const serverAllows = ref(true)
const effectiveEnabled = ref(false)
const passphrase = ref('')
const connectUrl = ref(null)
const debugOverlay = ref(false)
const qrLightboxDataUrl = ref(null)
const regenerating = ref(false)
const savingDebugOverlay = ref(false)
const error = ref(null)
const lightboxOpen = ref(false)

// Accelerating stops rather than a linear range — small, precise steps
// where operators actually need them (a clean network can shave off
// increments of 60ms), coarser ones at the high end where the exact
// value matters far less than "enough headroom for a rough network."
// A native <input type="range"> can't do non-uniform steps directly,
// so the slider itself moves over these stops' INDEX (0-6) and this
// array is the lookup table back to a real ms value — see
// srtLatencyIndex/srtLatencyMs below.
const SRT_LATENCY_STOPS_MS = [60, 120, 180, 240, 300, 450, 600]
const srtLatencyIndex = ref(closestStopIndex(120))
const savingLatency = ref(false)

function closestStopIndex(ms) {
  let best = 0
  let bestDiff = Infinity
  SRT_LATENCY_STOPS_MS.forEach((stop, i) => {
    const diff = Math.abs(stop - ms)
    if (diff < bestDiff) {
      bestDiff = diff
      best = i
    }
  })
  return best
}

function applyStatus(data) {
  localEnabled.value = data.local_enabled
  serverAllows.value = data.server_allows
  effectiveEnabled.value = data.effective_enabled
  passphrase.value = data.passphrase
  connectUrl.value = data.connect_url
  debugOverlay.value = data.debug_overlay
  // Backend allows a wider range than these 7 stops (e.g. legacy data,
  // or a value set some other way) — snap the slider to whichever stop
  // is closest rather than failing to represent it at all.
  srtLatencyIndex.value = closestStopIndex(data.srt_latency_ms)
}

// Generated client-side (no qrencode/system package needed) — the URL is
// already fully known from the API response, so there's nothing a
// server-rendered image would add. Lightbox-sized only (no inline preview,
// to keep the page from scrolling) — meant to be read by a phone camera
// from normal TV-viewing distance, not up close at the kiosk screen.
watch(connectUrl, async (url) => {
  qrLightboxDataUrl.value = url ? await QRCode.toDataURL(url, { width: 720, margin: 2 }) : null
}, { immediate: true })

function openLightbox() {
  if (qrLightboxDataUrl.value) lightboxOpen.value = true
}

function closeLightbox() {
  lightboxOpen.value = false
}

function onKeydown(event) {
  if (event.key === 'Escape' && lightboxOpen.value) closeLightbox()
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))

async function load() {
  try {
    applyStatus(await api.srtSinkStatus())
  } catch {
    // leave blank rather than erroring out the page
  }
}

async function regenerate() {
  regenerating.value = true
  error.value = null
  try {
    applyStatus(await api.regenerateSrtSinkPassphrase())
  } catch (err) {
    error.value = err.message
  } finally {
    regenerating.value = false
  }
}

async function setSrtLatency(index) {
  if (index === srtLatencyIndex.value || savingLatency.value) return
  const previousIndex = srtLatencyIndex.value
  // Optimistic: move the slider immediately (it's the input the user
  // just interacted with) and roll back only if the save fails.
  srtLatencyIndex.value = index
  savingLatency.value = true
  error.value = null
  try {
    applyStatus(await api.setSrtSinkLatency(SRT_LATENCY_STOPS_MS[index]))
  } catch (err) {
    srtLatencyIndex.value = previousIndex
    error.value = err.message
  } finally {
    savingLatency.value = false
  }
}

async function setDebugOverlaySetting(value) {
  if (value === debugOverlay.value || savingDebugOverlay.value) return
  savingDebugOverlay.value = true
  error.value = null
  try {
    applyStatus(await api.setSrtSinkDebugOverlay(value))
  } catch (err) {
    error.value = err.message
  } finally {
    savingDebugOverlay.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="settings-page">
    <section class="tile panel">
      <div class="panel-title"><h2>{{ t('settingsLayout.videoReceiver') }}</h2></div>
      <p class="hint">{{ t('settings.srtSink.intro') }}</p>

      <!-- On/off lives in Settings > Advanced (this page is only in the
           rail while it's on) — this is just a fallback if someone lands
           here with it off. -->
      <p v-if="!localEnabled" class="hint note">{{ t('settings.advanced.featureOffHint') }}</p>
      <p v-else-if="!serverAllows" class="pill warn note">{{ t('settings.srtSink.serverDisabled') }}</p>

      <div v-if="localEnabled && (passphrase || connectUrl)" class="info-grid">
        <template v-if="passphrase">
          <span class="label">{{ t('settings.srtSink.passphrase') }}</span>
          <code>{{ passphrase }}</code>
          <button type="button" class="tile action" :disabled="regenerating" @click="regenerate">
            {{ regenerating ? t('settings.srtSink.regenerating') : t('settings.srtSink.regenerate') }}
          </button>
        </template>
        <template v-if="connectUrl">
          <span class="label">{{ t('settings.srtSink.connectWith') }}</span>
          <code class="connect-url">{{ connectUrl }}</code>
          <button type="button" class="tile action" @click="openLightbox">
            {{ t('settings.srtSink.displayQrCode') }}
          </button>
        </template>
      </div>
      <p v-if="error" class="pill warn note">{{ error }}</p>
    </section>

    <section v-if="localEnabled" class="tile panel">
      <div class="panel-title"><h2>{{ t('settings.srtSink.playbackTitle') }}</h2></div>

      <div class="setting">
        <div class="setting-text">
          <span class="setting-name">{{ t('settings.srtSink.latencyTitle') }}</span>
          <span class="hint">{{ t('settings.srtSink.latencyHint') }}</span>
        </div>
        <div class="latency-row">
          <input
            type="range"
            min="0"
            :max="SRT_LATENCY_STOPS_MS.length - 1"
            step="1"
            :value="srtLatencyIndex"
            :disabled="savingLatency"
            class="latency-slider"
            @change="setSrtLatency(Number($event.target.value))"
          />
          <span class="latency-value">{{ SRT_LATENCY_STOPS_MS[srtLatencyIndex] }} ms</span>
        </div>
      </div>

      <div class="setting">
        <div class="setting-text">
          <span class="setting-name">{{ t('settings.srtSink.debugOverlayTitle') }}</span>
          <span class="hint">{{ t('settings.srtSink.debugOverlayHint') }}</span>
        </div>
        <ToggleSwitch
          :model-value="debugOverlay"
          :disabled="savingDebugOverlay"
          @update:model-value="setDebugOverlaySetting"
        />
      </div>
    </section>

    <div v-if="lightboxOpen" class="lightbox" data-nav-modal @click="closeLightbox">
      <div class="lightbox-content" @click.stop>
        <img :src="qrLightboxDataUrl" :alt="t('settings.srtSink.connectWith')" class="qr-large" />
        <button type="button" class="tile action lightbox-close" data-nav-close @click="closeLightbox">
          {{ t('settings.srtSink.close') }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.hint { color: var(--text-dim); margin: 0; font-size: 0.9rem; }
.note { margin: 0.6rem 0 0; }
.label { color: var(--text-dim); font-weight: 600; }
/* label | value | button, one row each for passphrase and connect URL */
.info-grid {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.6rem 1rem;
  margin-top: 0.9rem;
}
.info-grid code {
  padding: 0.4rem 0.7rem;
  background: var(--bg);
  border-radius: 0.4rem;
  letter-spacing: 0.05em;
}
.connect-url {
  overflow-wrap: anywhere;
  font-size: 0.85rem;
  letter-spacing: normal !important;
}
.action {
  padding: 0.55rem 1.1rem;
  font-size: 0.95rem;
}
.setting {
  display: flex;
  align-items: center;
  gap: 1.5rem;
  padding: 0.6rem 0;
}
.setting + .setting { border-top: var(--line) solid var(--border); }
.setting-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.setting-name { font-weight: 600; }
.latency-row {
  flex: 0 0 45%;
  display: flex;
  align-items: center;
  gap: 1rem;
}
.latency-slider {
  flex: 1;
  height: 2.5rem;
}
.latency-value {
  flex-shrink: 0;
  min-width: 4.5rem;
  text-align: right;
  font-weight: 600;
  color: var(--accent);
}
.lightbox {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.85);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.lightbox-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1.5rem;
}
.qr-large {
  width: min(70vh, 70vw);
  height: min(70vh, 70vw);
  background: #fff;
  padding: 1.5rem;
  border-radius: 0.8rem;
}
.lightbox-close {
  padding: 0.9rem 2rem;
  font-size: 1.1rem;
}
</style>
