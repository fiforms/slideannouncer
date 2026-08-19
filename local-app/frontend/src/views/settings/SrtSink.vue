<script setup>
import { ref, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import QRCode from 'qrcode'
import { api } from '../../api.js'

const { t } = useI18n()

const localEnabled = ref(false)
const serverAllows = ref(true)
const effectiveEnabled = ref(false)
const passphrase = ref('')
const connectUrl = ref(null)
const qrDataUrl = ref(null)
const saving = ref(false)
const regenerating = ref(false)
const error = ref(null)
const copied = ref(false)

function applyStatus(data) {
  localEnabled.value = data.local_enabled
  serverAllows.value = data.server_allows
  effectiveEnabled.value = data.effective_enabled
  passphrase.value = data.passphrase
  connectUrl.value = data.connect_url
}

// Generated client-side (no qrencode/system package needed) — the URL is
// already fully known from the API response, so there's nothing a
// server-rendered image would add.
watch(connectUrl, async (url) => {
  qrDataUrl.value = url ? await QRCode.toDataURL(url, { width: 220, margin: 1 }) : null
}, { immediate: true })

async function load() {
  try {
    applyStatus(await api.srtSinkStatus())
  } catch {
    // leave blank rather than erroring out the page
  }
}

async function setEnabled(value) {
  if (value === localEnabled.value || saving.value) return
  saving.value = true
  error.value = null
  try {
    applyStatus(await api.setSrtSink(value))
  } catch (err) {
    error.value = err.message
  } finally {
    saving.value = false
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

async function copyConnectUrl() {
  if (!connectUrl.value) return
  await navigator.clipboard?.writeText(connectUrl.value)
  copied.value = true
  setTimeout(() => { copied.value = false }, 1500)
}

onMounted(load)
</script>

<template>
  <div>
    <h1>{{ t('settingsLayout.videoReceiver') }}</h1>

    <section class="block">
      <h2>{{ t('settings.srtSink.title') }}</h2>
      <p class="hint">{{ t('settings.srtSink.hint') }}</p>

      <div class="toggle-row" role="group">
        <button
          type="button"
          class="toggle-button"
          :class="{ active: !saving && localEnabled }"
          :disabled="saving"
          @click="setEnabled(true)"
        >
          {{ t('settings.srtSink.enabled') }}
        </button>
        <button
          type="button"
          class="toggle-button"
          :class="{ active: !saving && !localEnabled }"
          :disabled="saving"
          @click="setEnabled(false)"
        >
          {{ t('settings.srtSink.disabled') }}
        </button>
      </div>

      <p v-if="localEnabled && !serverAllows" class="pill warn">
        {{ t('settings.srtSink.serverDisabled') }}
      </p>

      <template v-if="localEnabled">
        <div v-if="passphrase" class="field">
          <span class="label">{{ t('settings.srtSink.passphrase') }}</span>
          <div class="passphrase-row">
            <code>{{ passphrase }}</code>
            <button type="button" class="tile action" :disabled="regenerating" @click="regenerate">
              {{ regenerating ? t('settings.srtSink.regenerating') : t('settings.srtSink.regenerate') }}
            </button>
          </div>
        </div>

        <div v-if="connectUrl" class="field">
          <span class="label">{{ t('settings.srtSink.connectWith') }}</span>
          <div class="connect-url-row">
            <code class="connect-url">{{ connectUrl }}</code>
            <button type="button" class="tile action" @click="copyConnectUrl">
              {{ copied ? t('settings.srtSink.copied') : t('settings.srtSink.copy') }}
            </button>
          </div>
          <img v-if="qrDataUrl" :src="qrDataUrl" :alt="t('settings.srtSink.connectWith')" class="qr-code" />
        </div>
      </template>

      <p v-if="error" class="pill warn">{{ error }}</p>
    </section>
  </div>
</template>

<style scoped>
h1 { margin-top: 0; }
.block {
  max-width: 32rem;
  margin-bottom: 2.5rem;
}
h2 {
  font-size: 1.1rem;
  margin-bottom: 0.5rem;
}
.hint {
  color: var(--text-dim);
}
.toggle-row {
  display: flex;
  gap: 1rem;
  margin: 1.25rem 0 1.5rem;
}
.toggle-button {
  flex: 1;
  padding: 1.1rem 1.5rem;
  font-size: 1.1rem;
  font-weight: 600;
  border-radius: 0.6rem;
  border: 2px solid var(--panel-hover, rgba(255, 255, 255, 0.12));
  background: transparent;
  color: var(--text);
}
.toggle-button:disabled {
  opacity: 0.6;
}
.toggle-button.active {
  border-color: var(--accent, #6c8cff);
  color: var(--accent, #6c8cff);
  background: var(--panel, rgba(108, 140, 255, 0.08));
}
.field {
  display: block;
  margin-bottom: 1.25rem;
}
.label {
  display: block;
  margin-bottom: 0.4rem;
  color: var(--text-dim);
}
.passphrase-row,
.connect-url-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.passphrase-row code,
.connect-url-row code {
  font-size: 1.1rem;
  padding: 0.5rem 0.8rem;
  background: var(--panel, rgba(255, 255, 255, 0.06));
  border-radius: 0.4rem;
  letter-spacing: 0.05em;
}
.connect-url-row {
  align-items: flex-start;
}
.connect-url {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 0.9rem;
  letter-spacing: normal;
}
.action {
  flex-shrink: 0;
  padding: 0.6rem 1.1rem;
  font-size: 0.95rem;
}
.qr-code {
  display: block;
  margin-top: 0.9rem;
  width: 220px;
  height: 220px;
  background: #fff;
  padding: 0.6rem;
  border-radius: 0.4rem;
}
</style>
