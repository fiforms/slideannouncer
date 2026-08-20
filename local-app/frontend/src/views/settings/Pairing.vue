<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'

const router = useRouter()
const { t } = useI18n()

// Fallback only — normally status.hostname (below) already holds this
// device's current effective hostname (device_uuid-derived until it's
// ever paired, see firstboot.py's set_hostname()), which is a much more
// useful default than a random one: it's stable and it's what's already
// printed/spoken about this specific unit. This only kicks in if the
// status call itself fails.
function randomDeviceName() {
  const suffix = Math.floor(1000 + Math.random() * 9000)
  return `SlideAnnouncer-${suffix}`
}

const status = ref(null)
const code = ref('')
const deviceName = ref('')

const nameInput = ref(null)
const codeInput = ref(null)

// idle -> pairing -> error (paired devices just fall through to the status
// card below once `status.paired` flips true, so there's no "success" state
// to hold here — reloading status IS the success path)
const state = ref('idle')
const errorMessage = ref(null)

// Set once, only by pair() succeeding in this session — not derived from
// status.paired, which stays true across a reboot too and would otherwise
// show the reboot banner forever. This device's hostname was just
// (re)derived from the name typed above (see pairing.py's pair()), and
// won't actually take effect until the next boot — see firstboot.py's
// set_hostname().
const justPaired = ref(false)

async function loadStatus() {
  status.value = await api.localStatus().catch(() => null)
  if (!deviceName.value) deviceName.value = status.value?.hostname || randomDeviceName()
}

onMounted(loadStatus)

async function pair() {
  if (code.value.length !== 6) return
  state.value = 'pairing'
  errorMessage.value = null
  try {
    await api.pair(code.value.trim(), deviceName.value.trim() || randomDeviceName())
    state.value = 'idle'
    justPaired.value = true
    await loadStatus()
  } catch (err) {
    errorMessage.value = err.message
    state.value = 'error'
  }
}

const rebooting = ref(false)
const rebootError = ref(null)

async function rebootNow() {
  rebooting.value = true
  rebootError.value = null
  try {
    await api.reboot()
    // Same TypeError-means-it-actually-rebooted reasoning as unpair()'s
    // reboot call below.
  } catch (err) {
    if (!(err instanceof TypeError)) rebootError.value = err.message
  } finally {
    rebooting.value = false
  }
}

function formatTimestamp(isoString) {
  if (!isoString) return null
  const date = new Date(isoString)
  const datePart = date.toLocaleDateString(undefined, { month: 'long', day: 'numeric' })
  const timePart = date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }).replace(' ', '').toLowerCase()
  return `${datePart}, ${timePart}`
}

function pairedSince(isoString) {
  if (!isoString) return null
  const ms = Date.now() - new Date(isoString).getTime()
  const minutes = Math.round(ms / 60000)
  if (minutes < 60) return minutes <= 1 ? t('settings.pairing.justNow') : t('settings.pairing.minutesAgo', { n: minutes })
  const hours = Math.round(minutes / 60)
  if (hours < 24) return hours === 1 ? t('settings.pairing.hourAgo') : t('settings.pairing.hoursAgo', { n: hours })
  const days = Math.round(hours / 24)
  return days === 1 ? t('settings.pairing.dayAgo') : t('settings.pairing.daysAgo', { n: days })
}

const confirmingUnpair = ref(false)
const unpairing = ref(false)
const unpairError = ref(null)

async function unpair() {
  unpairing.value = true
  unpairError.value = null
  try {
    await api.unpair()
    await api.reboot()
    // reboot() rejects only for a real backend-reported failure (see its
    // own try/catch pattern elsewhere) — a dropped connection means the
    // device is actually rebooting, which is the expected outcome here.
  } catch (err) {
    if (!(err instanceof TypeError)) unpairError.value = err.message
  } finally {
    unpairing.value = false
    confirmingUnpair.value = false
  }
}

function goToSlideshow() {
  router.push('/kiosk')
}
</script>

<template>
  <div>
    <h1>{{ t('settings.pairing.title') }}</h1>

    <template v-if="status?.paired">
      <section v-if="justPaired" class="block reboot-banner tile">
        <p class="reboot-message">{{ t('settings.pairing.rebootHint') }}</p>
        <button class="tile action primary" @click="rebootNow" :disabled="rebooting">
          {{ rebooting ? t('settings.pairing.rebooting') : t('settings.pairing.rebootButton') }}
        </button>
        <p v-if="rebootError" class="pill warn">{{ rebootError }}</p>
      </section>

      <section class="block">
        <div class="result tile">
          <div class="row">
            <span class="label">{{ t('settings.pairing.status') }}</span>
            <span class="pill ok">{{ t('settings.pairing.paired') }}</span>
          </div>
          <div v-if="status.device_name" class="row">
            <span class="label">{{ t('settings.pairing.name') }}</span>
            <span>{{ status.device_name }}</span>
          </div>
          <div v-if="status.entity_name" class="row">
            <span class="label">{{ t('settings.pairing.entity') }}</span>
            <span>{{ status.entity_name }}</span>
          </div>         <div v-if="status.paired_at" class="row">
            <span class="label">{{ t('settings.pairing.pairedSince') }}</span>
            <span>{{ pairedSince(status.paired_at) }}</span>
          </div>
          <div v-if="status.paired_at" class="row">
              <span class="label">{{ t('settings.pairing.lastHeartbeat') }}</span>
              <span>{{ formatTimestamp(status.heartbeat?.last_success_at) ?? t('common.never') }}</span>
              <span v-if="status.heartbeat?.last_error">{{ t('settings.pairing.lastHeartbeatError') }}</span>
              <span v-if="status.heartbeat?.last_error">{{ status.heartbeat.last_error }}</span>
          </div>
        </div>
        <button class="tile action" @click="goToSlideshow">{{ t('settings.pairing.goToSlideshow') }}</button>
      </section>

      <section class="block">
        <h2>{{ t('settings.pairing.unpairTitle') }}</h2>
        <p class="hint">
          {{ t('settings.pairing.unpairHint') }}
        </p>

        <div v-if="unpairing" class="status-block">
          <p class="pill warn">{{ t('settings.pairing.unpairing') }}</p>
        </div>
        <div v-else-if="!confirmingUnpair" class="actions">
          <button class="tile action danger" @click="confirmingUnpair = true">{{ t('settings.pairing.unpairButton') }}</button>
        </div>
        <div v-else class="actions">
          <button class="tile action danger" @click="unpair">{{ t('settings.pairing.unpairConfirm') }}</button>
          <button class="tile action" @click="confirmingUnpair = false">{{ t('common.cancel') }}</button>
        </div>
        <p v-if="unpairError" class="pill warn">{{ unpairError }}</p>
      </section>
    </template>

    <form v-else class="form" @submit.prevent="pair">
      <p class="hint">
        {{ t('settings.pairing.generateHint') }}
      </p>

      <label class="field">
        <span>{{ t('settings.pairing.deviceNameLabel') }} <span class="optional">{{ t('settings.pairing.optional') }}</span></span>
        <input
          type="text"
          v-model="deviceName"
          ref="nameInput"
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
  </div>
</template>

<style scoped>
h1 { margin-top: 0; }
h2 {
  font-size: 1.1rem;
  margin-bottom: 0.5rem;
}
.block {
  max-width: 32rem;
  margin-bottom: 2.5rem;
}
.result {
  padding: 1rem 1.25rem;
  margin-bottom: 1rem;
}
.row {
  display: flex;
  justify-content: space-between;
  margin-bottom: 0.5rem;
}
.row:last-child { margin-bottom: 0; }
.label { color: var(--text-dim); }
.hint { color: var(--text-dim); margin-top: 0; }
.actions {
  display: flex;
  gap: 1rem;
}
.action {
  padding: 0.9rem 1.6rem;
  font-size: 1.05rem;
}
.action.danger {
  border-color: var(--danger);
  color: var(--danger);
}
.action.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.reboot-banner {
  border-color: var(--accent);
  padding: 1rem 1.25rem;
}
.reboot-message {
  margin: 0 0 0.85rem;
  font-weight: 600;
}
.status-block { margin-top: 0.5rem; }
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
</style>
