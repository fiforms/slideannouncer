<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import QRCode from 'qrcode'
import { api } from '../api.js'

// The unpaired half of Settings > Pairing (views/settings/Pairing.vue),
// shared with the setup wizard's Pairing step (views/setup/SetupPairing.vue).
// Emits `paired` once the server accepts the code; what happens next (the
// reboot banner, or the wizard's next step) is the parent's call.
const props = defineProps({
  // Where a church's own staff generate a pairing code from — pairing.py's
  // read_server_url() (this device's configured AnnouncementSlides server),
  // exposed read-only as /api/local/status's server_url.
  serverUrl: { type: String, default: null },
  // Prefill for the device-name field — normally the device's current
  // hostname (device_uuid-derived until it's ever paired, see firstboot.py's
  // set_hostname()), which is stable and already what's printed/spoken
  // about this specific unit.
  defaultName: { type: String, default: null },
  // The wizard asks for the name on its own step, so it hides the field
  // here and just passes the name through.
  showNameField: { type: Boolean, default: true },
})
const emit = defineEmits(['paired'])

const { t } = useI18n()

// Fallback only, for when no defaultName could be fetched.
function randomDeviceName() {
  const suffix = Math.floor(1000 + Math.random() * 9000)
  return `SlideAnnouncer-${suffix}`
}

const code = ref('')
const deviceName = ref(props.defaultName || '')
watch(() => props.defaultName, (name) => {
  if (!deviceName.value && name) deviceName.value = name
})

const codeInput = ref(null)

// idle -> pairing -> error
const state = ref('idle')
const errorMessage = ref(null)

const pairingUrl = computed(() => props.serverUrl ? `${props.serverUrl}/slide-announcers` : null)
const pairingQrDataUrl = ref(null)
const pairingQrLightboxDataUrl = ref(null)
const pairingLightboxOpen = ref(false)

// Same two-size approach as SrtSink.vue's QR code — a small inline
// preview plus a much larger one meant to be scanned from across a room,
// since this is the one screen someone unboxing a fresh device is most
// likely to be standing right in front of without a computer handy.
watch(pairingUrl, async (url) => {
  pairingQrDataUrl.value = url ? await QRCode.toDataURL(url, { width: 160, margin: 1 }) : null
  pairingQrLightboxDataUrl.value = url ? await QRCode.toDataURL(url, { width: 720, margin: 2 }) : null
}, { immediate: true })

function openPairingLightbox() {
  if (pairingQrLightboxDataUrl.value) pairingLightboxOpen.value = true
}

function closePairingLightbox() {
  pairingLightboxOpen.value = false
}

function onKeydown(event) {
  if (event.key === 'Escape' && pairingLightboxOpen.value) closePairingLightbox()
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))

async function pair() {
  if (code.value.length !== 6) return
  state.value = 'pairing'
  errorMessage.value = null
  try {
    await api.pair(code.value.trim(), deviceName.value.trim() || randomDeviceName())
    state.value = 'idle'
    emit('paired')
  } catch (err) {
    errorMessage.value = err.message
    state.value = 'error'
  }
}
</script>

<template>
  <form class="form" @submit.prevent="pair">
    <p class="hint">
      {{ pairingUrl ? t('settings.pairing.generateHintUrl') : t('settings.pairing.generateHint') }}
    </p>

    <div v-if="pairingUrl" class="generate-row">
      <code class="pairing-url">{{ pairingUrl }}</code>
      <button type="button" class="tile action" @click="openPairingLightbox">
        {{ t('settings.pairing.showQrCode') }}
      </button>
    </div>
    <img v-if="pairingQrDataUrl" :src="pairingQrDataUrl" :alt="pairingUrl" class="qr-code" />

    <label v-if="showNameField" class="field">
      <span>{{ t('settings.pairing.deviceNameLabel') }} <span class="optional">{{ t('settings.pairing.optional') }}</span></span>
      <input
        type="text"
        v-model="deviceName"
        autocomplete="off"
        @keydown.enter.prevent="codeInput?.focus()"
      >
    </label>

    <label class="field">
      <span>{{ t('settings.pairing.pairingCodeLabel') }}</span>
      <input
        type="text"
        v-model="code"
        ref="codeInput"
        inputmode="numeric"
        maxlength="6"
        autofocus
        autocomplete="off"
        class="code-input"
        @keydown.enter.prevent="pair()"
      >
    </label>

    <p v-if="state === 'error'" class="pill warn">{{ errorMessage }}</p>

    <button type="submit" class="tile action" :disabled="code.length !== 6 || state === 'pairing'">
      {{ state === 'pairing' ? t('settings.pairing.pairing') : t('settings.pairing.pairButton') }}
    </button>
  </form>

  <div v-if="pairingLightboxOpen" class="lightbox" data-nav-modal @click="closePairingLightbox">
    <div class="lightbox-content" @click.stop>
      <img :src="pairingQrLightboxDataUrl" :alt="pairingUrl" class="qr-large" />
      <p class="pairing-url lightbox-url">{{ pairingUrl }}</p>
      <button type="button" class="tile action lightbox-close" data-nav-close @click="closePairingLightbox">
        {{ t('settings.pairing.close') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.hint { color: var(--text-dim); margin-top: 0; }
.action {
  padding: 0.9rem 1.6rem;
  font-size: 1.05rem;
}
.form {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  max-width: 28rem;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.optional { color: var(--text-dim); font-weight: normal; }
.code-input {
  font-size: 1.75rem;
  letter-spacing: 0.4em;
  text-align: center;
}
.generate-row {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
}
.pairing-url {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 0.9rem;
  padding: 0.5rem 0.8rem;
  background: var(--panel, rgba(255, 255, 255, 0.06));
  border-radius: 0.4rem;
}
.qr-code {
  display: block;
  margin-top: 0.9rem;
  width: 160px;
  height: 160px;
  background: #fff;
  padding: 0.5rem;
  border-radius: 0.4rem;
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
  gap: 1.25rem;
  max-width: 90vw;
}
.qr-large {
  width: min(70vh, 70vw);
  height: min(70vh, 70vw);
  background: #fff;
  padding: 1.5rem;
  border-radius: 0.8rem;
}
.lightbox-url {
  color: var(--text-dim);
  text-align: center;
}
.lightbox-close {
  padding: 0.9rem 2rem;
  font-size: 1.1rem;
}
</style>
