<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import QRCode from 'qrcode'
import { api } from '../../api.js'
import Dropdown from '../../components/Dropdown.vue'
import ToggleSwitch from '../../components/ToggleSwitch.vue'

const { t } = useI18n()

const localEnabled = ref(false)
const serverAllows = ref(true)
const mode = ref('srt') // 'srt' | 'rist_unicast' | 'rist_multicast' — see backend srt_sink.MODES
const ristSupported = ref(true)
const passphrase = ref('')
const connectUrl = ref(null)
const debugOverlay = ref(false)
const srtLatencyMs = ref(120)
const ristBufferMs = ref(500)
const ristEncryptionBits = ref(128)
const multicastGroup = ref('')
const multicastPort = ref(5000)
const multicastPassphrase = ref('')
const qrLightboxDataUrl = ref(null)
const regenerating = ref(false)
const savingMode = ref(false)
const savingDebugOverlay = ref(false)
const error = ref(null)
const lightboxOpen = ref(false)
// Multicast over WiFi is unreliable (sent at the lowest basic rate, no
// per-client retransmission), so the page warns when that's the link.
const onWifi = ref(false)

const isRist = computed(() => mode.value !== 'srt')
const isMulticast = computed(() => mode.value === 'rist_multicast')

const modeOptions = computed(() => [
  { value: 'srt', label: t('settings.srtSink.modeSrt') },
  // Hidden rather than failing on save when this device's ffmpeg has no
  // librist (backend srt_sink.rist_supported()).
  ...(ristSupported.value || isRist.value ? [
    { value: 'rist_unicast', label: t('settings.srtSink.modeRistUnicast') },
    { value: 'rist_multicast', label: t('settings.srtSink.modeRistMulticast') },
  ] : []),
])

const encryptionOptions = [
  { value: 128, label: 'AES-128' },
  { value: 256, label: 'AES-256' },
]

// Accelerating stops rather than a linear range — small, precise steps
// where operators actually need them, coarser ones at the high end where
// the exact value matters far less than "enough headroom for a rough
// network." A native <input type="range"> can't do non-uniform steps
// directly, so the slider moves over a stop INDEX and these arrays map it
// back to a real ms value. SRT's latency and RIST's receive buffer are
// the same kind of knob (time allowed to recover lost packets) with
// different sensible ranges — librist's own default is 1000ms.
const SRT_LATENCY_STOPS_MS = [60, 120, 180, 240, 300, 450, 600]
const RIST_BUFFER_STOPS_MS = [100, 200, 300, 500, 750, 1000, 1500, 2000]
const bufferStops = computed(() => (isRist.value ? RIST_BUFFER_STOPS_MS : SRT_LATENCY_STOPS_MS))
const bufferMs = computed(() => (isRist.value ? ristBufferMs.value : srtLatencyMs.value))
const bufferIndex = computed(() => closestStopIndex(bufferStops.value, bufferMs.value))
const savingLatency = ref(false)

function closestStopIndex(stops, ms) {
  let best = 0
  let bestDiff = Infinity
  stops.forEach((stop, i) => {
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
  mode.value = data.mode
  ristSupported.value = data.rist_supported
  passphrase.value = data.passphrase
  connectUrl.value = data.connect_url
  debugOverlay.value = data.debug_overlay
  srtLatencyMs.value = data.srt_latency_ms
  ristBufferMs.value = data.rist_buffer_ms
  ristEncryptionBits.value = data.rist_encryption_bits
  multicastGroup.value = data.multicast_group
  multicastPort.value = data.multicast_port
  multicastPassphrase.value = data.multicast_passphrase
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

async function loadNetworkType() {
  try {
    onWifi.value = (await api.networkStatus()).connection_type === 'wifi'
  } catch {
    onWifi.value = false
  }
}

async function saveSettings(changes) {
  error.value = null
  try {
    applyStatus(await api.setSrtSinkSettings(changes))
    return true
  } catch (err) {
    error.value = err.message
    return false
  }
}

async function setMode(value) {
  savingMode.value = true
  await saveSettings({ mode: value })
  savingMode.value = false
}

async function setEncryption(value) {
  await saveSettings({ rist_encryption_bits: value })
}

// SRT / RIST Unicast passphrase: generated on first enable, now also
// editable by hand (e.g. to match one a sender is already set up with).
const editingPassphrase = ref(false)
const passphraseDraft = ref('')
const passphraseInput = ref(null)

async function editPassphrase() {
  passphraseDraft.value = passphrase.value
  editingPassphrase.value = true
  await nextTick()
  passphraseInput.value?.focus()
}

async function savePassphrase() {
  if (await saveSettings({ passphrase: passphraseDraft.value })) editingPassphrase.value = false
}

// RIST Multicast: group, port and passphrase all come from the sender's
// own config, so they're typed in and saved together.
const multicastDraft = ref({ group: '', port: 5000, passphrase: '' })
const savingMulticast = ref(false)

watch([multicastGroup, multicastPort, multicastPassphrase], ([group, port, pass]) => {
  multicastDraft.value = { group, port, passphrase: pass }
}, { immediate: true })

async function saveMulticast() {
  savingMulticast.value = true
  await saveSettings({
    multicast_group: multicastDraft.value.group,
    multicast_port: Number(multicastDraft.value.port),
    multicast_passphrase: multicastDraft.value.passphrase,
  })
  savingMulticast.value = false
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

async function setBuffer(index) {
  if (index === bufferIndex.value || savingLatency.value) return
  const ms = bufferStops.value[index]
  const rist = isRist.value
  const previous = rist ? ristBufferMs.value : srtLatencyMs.value
  // Optimistic: move the slider immediately (it's the input the user
  // just interacted with) and roll back only if the save fails.
  if (rist) ristBufferMs.value = ms
  else srtLatencyMs.value = ms
  savingLatency.value = true
  error.value = null
  try {
    applyStatus(rist ? await api.setSrtSinkSettings({ rist_buffer_ms: ms }) : await api.setSrtSinkLatency(ms))
  } catch (err) {
    if (rist) ristBufferMs.value = previous
    else srtLatencyMs.value = previous
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

onMounted(() => {
  load()
  loadNetworkType()
})
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
      <template v-else>
        <p v-if="!serverAllows" class="pill warn note">{{ t('settings.srtSink.serverDisabled') }}</p>

        <div class="form-grid">
          <span class="label">{{ t('settings.srtSink.mode') }}</span>
          <div class="row">
            <Dropdown :model-value="mode" :options="modeOptions" :disabled="savingMode" @update:model-value="setMode" />
            <Dropdown
              v-if="isRist"
              :model-value="ristEncryptionBits"
              :options="encryptionOptions"
              @update:model-value="setEncryption"
            />
          </div>

          <!-- SRT / RIST Unicast: this device listens; the passphrase is
               generated but editable. -->
          <template v-if="!isMulticast">
            <span class="label">{{ t('settings.srtSink.passphrase') }}</span>
            <form v-if="editingPassphrase" class="row" @submit.prevent="savePassphrase">
              <input ref="passphraseInput" v-model="passphraseDraft" type="text" class="grow" autocomplete="off">
              <button type="submit" class="tile action">{{ t('common.save') }}</button>
              <button type="button" class="tile action" @click="editingPassphrase = false">{{ t('common.cancel') }}</button>
            </form>
            <div v-else class="row">
              <code class="grow">{{ passphrase || '—' }}</code>
              <button type="button" class="tile action" @click="editPassphrase">{{ t('settings.srtSink.edit') }}</button>
              <button type="button" class="tile action" :disabled="regenerating" @click="regenerate">
                {{ regenerating ? t('settings.srtSink.regenerating') : t('settings.srtSink.regenerate') }}
              </button>
            </div>
          </template>
        </div>

        <!-- RIST Multicast: joins the group the sender transmits to; all
             three values must match the sender's own settings. -->
        <template v-if="isMulticast">
          <p v-if="onWifi" class="wifi-warning">{{ t('settings.srtSink.multicastWifiWarning') }}</p>
          <form class="multicast-form" @submit.prevent="saveMulticast">
            <label class="field">
              <span class="label">{{ t('settings.srtSink.multicastGroup') }}</span>
              <input v-model="multicastDraft.group" type="text" placeholder="239.1.2.3" autocomplete="off">
            </label>
            <label class="field port">
              <span class="label">{{ t('settings.srtSink.port') }}</span>
              <input v-model="multicastDraft.port" type="text" inputmode="numeric" autocomplete="off">
            </label>
            <label class="field grow">
              <span class="label">{{ t('settings.srtSink.passphrase') }}</span>
              <input v-model="multicastDraft.passphrase" type="text" :placeholder="t('settings.srtSink.multicastPassphraseHint')" autocomplete="off">
            </label>
            <button type="submit" class="tile action" :disabled="savingMulticast">{{ t('common.save') }}</button>
          </form>
        </template>

        <div v-if="connectUrl" class="form-grid connect">
          <span class="label">{{ isMulticast ? t('settings.srtSink.senderUrl') : t('settings.srtSink.connectWith') }}</span>
          <div class="row">
            <code class="connect-url grow">{{ connectUrl }}</code>
            <button type="button" class="tile action" @click="openLightbox">
              {{ t('settings.srtSink.displayQrCode') }}
            </button>
          </div>
        </div>
      </template>
      <p v-if="error" class="pill warn note">{{ error }}</p>
    </section>

    <section v-if="localEnabled" class="tile panel">
      <div class="panel-title"><h2>{{ t('settings.srtSink.playbackTitle') }}</h2></div>

      <div class="setting">
        <div class="setting-text">
          <span class="setting-name">{{ isRist ? t('settings.srtSink.ristBufferTitle') : t('settings.srtSink.latencyTitle') }}</span>
          <span class="hint">{{ t('settings.srtSink.latencyHint') }}</span>
        </div>
        <div class="latency-row">
          <input
            type="range"
            min="0"
            :max="bufferStops.length - 1"
            step="1"
            :value="bufferIndex"
            :disabled="savingLatency"
            class="latency-slider"
            @change="setBuffer(Number($event.target.value))"
          />
          <span class="latency-value">{{ bufferStops[bufferIndex] }} ms</span>
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
/* label | controls, one row per setting */
.form-grid {
  display: grid;
  grid-template-columns: 8rem minmax(0, 1fr);
  align-items: center;
  gap: 0.6rem 1rem;
  margin-top: 0.9rem;
}
.row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  min-width: 0;
}
.grow { flex: 1; min-width: 0; }
code {
  padding: 0.4rem 0.7rem;
  background: var(--bg);
  border-radius: 0.4rem;
  letter-spacing: 0.05em;
}
.connect-url {
  overflow-wrap: anywhere;
  font-size: 0.85rem;
  letter-spacing: normal;
}
input[type="text"] {
  padding: 0.5rem 0.8rem;
  font-size: 1rem;
}
.action {
  flex-shrink: 0;
  padding: 0.55rem 1.1rem;
  font-size: 0.95rem;
}
.multicast-form {
  display: flex;
  align-items: flex-end;
  gap: 0.75rem;
  margin-top: 0.9rem;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  min-width: 0;
}
.field input { width: 100%; }
.field:first-child { flex: 0 0 9.5rem; }
.field.port { flex: 0 0 5.5rem; }
.wifi-warning {
  margin: 0.9rem 0 0;
  padding: 0.6rem 0.9rem;
  border: var(--line-thick) solid var(--danger);
  border-radius: 0.5rem;
  background: rgba(255, 107, 107, 0.12);
  color: var(--danger);
  font-weight: 700;
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
