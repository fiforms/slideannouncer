<script setup>
import { computed, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { openCaptivePortal } from '../../captivePortal.js'

const props = defineProps({ ssid: { type: String, required: true } })
const route = useRoute()
const router = useRouter()
const { t, te } = useI18n()
const networkBase = route.meta.networkBase || '/settings/network'

const secured = route.query.secured !== '0'
// Set by WifiList for 802.1X ("company login") and WEP networks, which this
// screen has no way to join — say so instead of offering a doomed password.
const unsupportedKind = route.query.unsupported || null
const password = ref('')
const showPassword = ref(false)

// idle -> connecting -> success | error
const state = ref('idle')
const errorMessage = ref(null)
const connectivity = ref(null)
// What the backend knew about a failed join (see network.ConnectError):
// a reason code to translate, nmcli's raw message and the journal lines.
const failure = ref(null)
const showDetails = ref(false)

// Full internet = the happy path: a big confirmation, then back to the
// Network page on its own after REDIRECT_SECONDS. Connected-but-limited
// (captive portal, no route out) stays put with a warning instead, since
// that needs someone to read it and act.
const REDIRECT_SECONDS = 5
const online = computed(() => connectivity.value === 'full')
const secondsLeft = ref(REDIRECT_SECONDS)
let redirectTimer = null

async function connect() {
  state.value = 'connecting'
  errorMessage.value = null
  failure.value = null
  showDetails.value = false
  try {
    const result = await api.networkConnect(props.ssid, secured ? password.value : null)
    connectivity.value = result.connectivity
    state.value = 'success'
    if (online.value) startRedirect()
  } catch (err) {
    const body = err.body
    const key = `settings.wifiConnect.failure.${body?.reason}`
    errorMessage.value = body?.reason && te(key) ? t(key) : err.message
    failure.value = body?.reason ? body : null
    state.value = 'error'
  }
}

function goToDiagnostics() {
  router.push(`${networkBase}/diagnostics`)
}

function startRedirect() {
  secondsLeft.value = REDIRECT_SECONDS
  redirectTimer = setInterval(() => {
    secondsLeft.value -= 1
    if (secondsLeft.value <= 0) done()
  }, 1000)
}

onUnmounted(() => clearInterval(redirectTimer))

const openingPortal = ref(false)

async function signInToPortal() {
  openingPortal.value = true
  try {
    await openCaptivePortal(networkBase)
  } catch (err) {
    errorMessage.value = err.message
    openingPortal.value = false
  }
}

function done() {
  clearInterval(redirectTimer)
  router.replace(networkBase)
}

const CONNECTIVITY_KEYS = ['full', 'limited', 'portal', 'none', 'unknown']
const connectivityLabel = computed(() =>
  t(`settings.network.connectivity.${CONNECTIVITY_KEYS.includes(connectivity.value) ? connectivity.value : 'unknown'}`))
</script>

<template>
  <div>
    <h1 v-if="state !== 'success'">{{ ssid }}</h1>

    <div v-if="unsupportedKind" class="status-block">
      <p class="pill warn">{{ t('settings.wifiConnect.unsupported', { kind: t(`settings.wifiList.kind.${unsupportedKind}`) }) }}</p>
    </div>

    <form v-else-if="state === 'idle' || state === 'error'" class="form" @submit.prevent="connect">
      <label v-if="secured" class="field">
        <span>{{ t('settings.wifiConnect.password') }}</span>
        <div class="password-row">
          <input
            :type="showPassword ? 'text' : 'password'"
            v-model="password"
            autofocus
            autocomplete="off"
          >
          <button type="button" class="tile toggle" @click="showPassword = !showPassword">
            {{ showPassword ? t('settings.wifiConnect.hide') : t('settings.wifiConnect.show') }}
          </button>
        </div>
      </label>
      <p v-else class="hint">{{ t('settings.wifiConnect.openNetworkHint') }}</p>

      <p v-if="state === 'error'" class="pill warn">{{ errorMessage }}</p>
      <div v-if="state === 'error'" class="failure-actions">
        <button v-if="failure" type="button" class="tile toggle" @click="showDetails = !showDetails">
          {{ showDetails ? t('settings.wifiConnect.hideDetails') : t('settings.wifiConnect.showDetails') }}
        </button>
        <button type="button" class="tile toggle" @click="goToDiagnostics">{{ t('settings.network.diagnostics') }}</button>
      </div>
      <div v-if="showDetails && failure" class="details">
        <p class="hint">{{ t('settings.wifiConnect.rawError') }}</p>
        <code>{{ failure.raw }}</code>
        <p v-if="failure.log?.length" class="hint">{{ t('settings.wifiConnect.logLines') }}</p>
        <pre v-if="failure.log?.length">{{ failure.log.join('\n') }}</pre>
        <p v-else-if="failure.log_restricted" class="hint">{{ t('settings.diagnostics.logRestricted') }}</p>
      </div>

      <button type="submit" class="tile action" :disabled="secured && !password">
        {{ t('settings.wifiConnect.connect') }}
      </button>
    </form>

    <div v-else-if="state === 'connecting'" class="status-block">
      <p>{{ t('settings.wifiConnect.connecting', { ssid }) }}</p>
    </div>

    <div v-else-if="state === 'success'" class="success tile" :class="{ 'success--limited': !online }">
      <div class="success-mark" aria-hidden="true">{{ online ? '✓' : '!' }}</div>
      <p class="success-title">
        {{ online ? t('settings.wifiConnect.successTitle') : t('settings.wifiConnect.limitedTitle') }}
      </p>
      <p class="success-detail">
        {{ online
          ? t('settings.wifiConnect.successDetail', { ssid })
          : t('settings.wifiConnect.limitedDetail', { ssid, connectivity: connectivityLabel }) }}
      </p>
      <div class="success-actions">
        <button
          v-if="connectivity === 'portal'"
          class="tile action primary"
          :disabled="openingPortal"
          @click="signInToPortal"
        >
          {{ t('settings.network.portalSignIn') }}
        </button>
        <button
          v-else-if="connectivity === 'limited'"
          class="tile action"
          :disabled="openingPortal"
          @click="signInToPortal"
        >
          {{ t('settings.network.portalTryAnyway') }}
        </button>
        <button class="tile action" @click="done">{{ t('settings.wifiConnect.done') }}</button>
      </div>
      <p v-if="connectivity === 'portal'" class="success-countdown">{{ t('settings.network.portalHint') }}</p>
      <p v-if="errorMessage" class="pill warn">{{ errorMessage }}</p>
      <p v-if="online" class="success-countdown">
        {{ t('settings.wifiConnect.returning', { seconds: secondsLeft }) }}
      </p>
    </div>
  </div>
</template>

<style scoped>
h1 { margin-top: 0; word-break: break-word; }
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
.password-row {
  display: flex;
  gap: 0.5rem;
}
.password-row input { flex: 1; }
.toggle { padding: 0.6rem 1rem; }
.hint { color: var(--text-dim); margin: 0.4rem 0; }
.failure-actions { display: flex; gap: 0.75rem; flex-wrap: wrap; }
.details { max-width: 40rem; font-size: 0.85rem; }
.details code, .details pre { display: block; margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.action {
  align-self: flex-start;
  padding: 0.9rem 1.8rem;
  font-size: 1.05rem;
}
.success {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.6rem;
  max-width: 32rem;
  padding: 2rem 2.5rem;
  text-align: center;
}
.success-mark {
  display: grid;
  place-items: center;
  width: 5rem;
  height: 5rem;
  border-radius: 50%;
  background: rgba(76, 175, 80, 0.15);
  border: var(--line-thick) solid var(--ok);
  color: var(--ok);
  font-size: 2.8rem;
  font-weight: 700;
  line-height: 1;
}
.success--limited .success-mark {
  background: rgba(255, 183, 77, 0.15);
  border-color: #ffb74d;
  color: #ffb74d;
}
.success-title {
  margin: 0.4rem 0 0;
  font-size: 1.6rem;
  font-weight: 700;
}
.success-detail {
  margin: 0 0 0.6rem;
  color: var(--text-dim);
  overflow-wrap: anywhere;
}
.success-actions {
  display: flex;
  gap: 0.75rem;
  justify-content: center;
}
.action.primary {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
  font-weight: 600;
}
.success-countdown {
  margin: 0;
  color: var(--text-dim);
  font-size: 0.9rem;
}
.status-block {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  align-items: flex-start;
}
</style>
