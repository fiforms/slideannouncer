<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { openCaptivePortal } from '../../captivePortal.js'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
// Shared with the setup wizard's Network step, so links stay relative to
// whichever of the two this is mounted under — see router.js's
// meta.networkBase.
const networkBase = route.meta.networkBase || '/settings/network'
const status = ref(null)
const hostname = ref(null)
const showAdvanced = ref(false)
// Slide Server row — a live check of the paired AnnouncementSlides
// server (backend server_check.py), fetched separately so a dead server
// can't hold up the rest of the page. Next to Internet, it tells an
// internet outage apart from a server one.
const server = ref(null)
const portalError = ref(null)
const openingPortal = ref(false)
// Set when captive_portal.py's watcher sends the kiosk back here after a
// portal sign-in (?portal=done / ?portal=timeout) — read once, then the
// query is dropped so it doesn't linger in the URL.
const portalReturn = ref(route.query.portal || null)
if (portalReturn.value) router.replace({ path: route.path })

async function signInToPortal() {
  openingPortal.value = true
  portalError.value = null
  try {
    await openCaptivePortal(networkBase)
  } catch (err) {
    portalError.value = err.message
    openingPortal.value = false
  }
}
const error = ref(null)
const forgetting = ref(false)
const forgetError = ref(null)

async function load() {
  error.value = null
  try {
    status.value = await api.networkStatus()
  } catch (err) {
    error.value = err.message
  }
}

// Same /api/local/status the System page reads its Device Info from.
async function loadHostname() {
  try {
    hostname.value = (await api.localStatus()).hostname
  } catch {
    // leave blank rather than erroring out the page
  }
}

async function loadServer() {
  server.value = null
  try {
    server.value = await api.networkServerCheck()
  } catch (err) {
    server.value = { state: 'unreachable', host: null, detail: err.message }
  }
}

const SERVER_STATE_KEYS = ['ok', 'error', 'unreachable', 'unconfigured']

function serverLabel(state) {
  return t(`settings.network.server.${SERVER_STATE_KEYS.includes(state) ? state : 'unreachable'}`)
}

onMounted(() => {
  load()
  loadHostname()
  loadServer()
})

function goToWifiSetup() {
  router.push(`${networkBase}/wifi`)
}

function goToDiagnostics() {
  router.push(`${networkBase}/diagnostics`)
}

async function forgetNetwork() {
  if (!status.value?.ssid) return
  forgetting.value = true
  forgetError.value = null
  try {
    await api.networkForget(status.value.ssid)
    await load()
  } catch (err) {
    forgetError.value = err.message
  } finally {
    forgetting.value = false
  }
}

// NetworkManager's own connectivity states (see backend network.py
// check_connectivity) — "portal" means a captive portal is intercepting
// traffic (e.g. a hotel/guest WiFi login page), not that we're offline.
const CONNECTIVITY_KEYS = ['full', 'limited', 'portal', 'none', 'unknown']

function connectivityLabel(connectivity) {
  const key = CONNECTIVITY_KEYS.includes(connectivity) ? connectivity : 'unknown'
  return t(`settings.network.connectivity.${key}`)
}
</script>

<template>
  <!-- Same layout as System.vue: the essentials up front, the addressing
       details (IP/subnet/gateway/DNS) behind an Advanced Info toggle so the
       page fits without scrolling. -->
  <div class="settings-page">
    <p v-if="error" class="pill warn">{{ error }}</p>
    <p v-if="portalReturn === 'done'" class="pill ok">{{ t('settings.network.portalDone') }}</p>
    <p v-else-if="portalReturn === 'timeout'" class="pill warn">{{ t('settings.network.portalTimeout') }}</p>

    <!-- Captive portal (guest/hotel WiFi login page) in the way — first
         thing on the page, since nothing else works until it's done. -->
    <section v-if="status?.connectivity === 'portal'" class="tile panel portal">
      <div class="panel-title"><h2>{{ t('settings.network.portalTitle') }}</h2></div>
      <p class="hint">{{ t('settings.network.portalHint') }}</p>
      <button class="tile action primary" :disabled="openingPortal" @click="signInToPortal">
        {{ t('settings.network.portalSignIn') }}
      </button>
      <p v-if="portalError" class="pill warn note">{{ portalError }}</p>
    </section>

    <section v-else class="tile panel">
      <div class="panel-title"><h2>{{ t('settings.network.title') }}</h2></div>
      <div v-if="status" class="info-grid">
        <span class="label">{{ t('settings.network.connection') }}</span>
        <span class="value">
          <span class="pill" :class="status.connected ? 'ok' : 'warn'">
            {{ status.connected ? status.connection_type : t('settings.network.disconnected') }}
          </span>
        </span>
        <template v-if="status.ssid">
          <span class="label">{{ t('settings.network.networkName') }}</span>
          <span class="value">{{ status.ssid }}</span>
        </template>
        <template v-if="status.signal !== null && status.signal !== undefined">
          <span class="label">{{ t('settings.network.signal') }}</span>
          <span class="value">{{ status.signal }}%</span>
        </template>
        <template v-if="status.connected">
          <span class="label">{{ t('settings.network.internet') }}</span>
          <span class="value">
            <span class="pill" :class="status.connectivity === 'full' ? 'ok' : 'warn'">
              {{ connectivityLabel(status.connectivity) }}
            </span>
          </span>
        </template>
        <span class="label">{{ t('settings.network.remoteServer') }}</span>
        <span class="value server">
          <span v-if="!server" class="hint">{{ t('settings.network.server.checking') }}</span>
          <template v-else>
            <span class="pill" :class="server.state === 'ok' ? 'ok' : 'warn'">{{ serverLabel(server.state) }}</span>
            <span v-if="server.state !== 'ok' && server.state !== 'unconfigured' && server.detail" class="hint">({{ server.detail }})</span>
          </template>
        </span>
        <span class="label">{{ t('settings.network.hostname') }}</span>
        <span class="value">{{ hostname || '—' }}</span>
      </div>
      <p v-else class="hint">{{ t('settings.network.loading') }}</p>

      <div class="actions">
        <button class="tile action" @click="goToWifiSetup">{{ t('settings.network.setupWifi') }}</button>
        <button
          v-if="status?.connection_type === 'wifi' && status?.connected"
          class="tile action"
          :disabled="forgetting"
          @click="forgetNetwork"
        >
          {{ forgetting ? t('settings.network.forgetting') : t('settings.network.forgetNetwork') }}
        </button>
        <!-- Connected with a route but no way out: a portal that blocks
             everything until sign-in looks exactly like this, and the
             probe can't tell — so offer its sign-in page regardless. -->
        <button
          v-if="status?.connectivity === 'limited' && status?.connection_type === 'wifi'"
          class="tile action"
          :disabled="openingPortal"
          @click="signInToPortal"
        >
          {{ t('settings.network.portalTryAnyway') }}
        </button>
        <button class="tile action" @click="goToDiagnostics">{{ t('settings.network.diagnostics') }}</button>
        <button v-if="status" class="tile action" :class="{ active: showAdvanced }" @click="showAdvanced = !showAdvanced">
          {{ showAdvanced ? t('settings.network.hideAdvanced') : t('settings.network.showAdvanced') }}
        </button>
      </div>
      <p v-if="forgetError" class="pill warn note">{{ forgetError }}</p>
    </section>

    <section v-if="showAdvanced && status" class="tile panel">
      <div class="panel-title"><h2>{{ t('settings.network.advancedTitle') }}</h2></div>
      <div class="info-grid">
        <span class="label">{{ t('settings.network.ipAddress') }}</span>
        <span class="value">{{ status.ip_addresses?.length ? status.ip_addresses.join(', ') : '—' }}</span>
        <span class="label">{{ t('settings.network.subnetMask') }}</span>
        <span class="value">{{ status.subnet_mask || '—' }}</span>
        <span class="label">{{ t('settings.network.defaultGateway') }}</span>
        <span class="value">{{ status.gateway || '—' }}</span>
        <span class="label">{{ t('settings.network.dnsServer', status.dns_servers?.length || 1) }}</span>
        <span class="value">{{ status.dns_servers?.length ? status.dns_servers.join(', ') : '—' }}</span>
      </div>
    </section>
  </div>
</template>

<style scoped>
.info-grid {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: center;
  gap: 0.35rem 1.5rem;
  margin-bottom: 1rem;
}
.label {
  color: var(--text-dim);
  font-weight: 600;
}
.value {
  color: var(--text);
  overflow-wrap: anywhere;
}
.hint {
  color: var(--text-dim);
  margin: 0 0 0.9rem;
}
.note {
  margin: 0.6rem 0 0;
}
.actions {
  display: flex;
  gap: 1rem;
  margin-top: 0.35rem;
  flex-wrap: wrap;
}
.action {
  padding: 0.75rem 1.5rem;
  font-size: 1rem;
}
.action.active {
  border-color: var(--accent);
  color: var(--accent);
}
.panel:last-child .info-grid { margin-bottom: 0; }
.server {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.6rem;
}
.server .hint { margin: 0; }
.portal { border-color: #ffb74d; }
.action.primary {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
  font-weight: 600;
}
</style>
