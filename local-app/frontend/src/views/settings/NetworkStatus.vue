<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'

const router = useRouter()
const { t } = useI18n()
const status = ref(null)
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

onMounted(load)

function goToWifiSetup() {
  router.push('/settings/network/wifi')
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
  <div>
    <h1>{{ t('settings.network.title') }}</h1>

    <p v-if="error" class="pill warn">{{ error }}</p>

    <div v-else-if="status" class="status-card tile">
      <div class="row">
        <span class="label">{{ t('settings.network.connection') }}</span>
        <span class="pill" :class="status.connected ? 'ok' : 'warn'">
          {{ status.connected ? status.connection_type : t('settings.network.disconnected') }}
        </span>
      </div>
      <div v-if="status.ssid" class="row">
        <span class="label">{{ t('settings.network.networkName') }}</span>
        <span>{{ status.ssid }}</span>
      </div>
      <div v-if="status.signal !== null && status.signal !== undefined" class="row">
        <span class="label">{{ t('settings.network.signal') }}</span>
        <span>{{ status.signal }}%</span>
      </div>
      <div class="row">
        <span class="label">{{ t('settings.network.ipAddress') }}</span>
        <span>{{ status.ip_addresses?.length ? status.ip_addresses.join(', ') : '—' }}</span>
      </div>
      <div v-if="status.subnet_mask" class="row">
        <span class="label">{{ t('settings.network.subnetMask') }}</span>
        <span>{{ status.subnet_mask }}</span>
      </div>
      <div v-if="status.gateway" class="row">
        <span class="label">{{ t('settings.network.defaultGateway') }}</span>
        <span>{{ status.gateway }}</span>
      </div>
      <div v-if="status.dns_servers?.length" class="row">
        <span class="label">{{ t('settings.network.dnsServer', status.dns_servers.length) }}</span>
        <span>{{ status.dns_servers.join(', ') }}</span>
      </div>
      <div v-if="status.connected" class="row">
        <span class="label">{{ t('settings.network.internet') }}</span>
        <span class="pill" :class="status.connectivity === 'full' ? 'ok' : 'warn'">
          {{ connectivityLabel(status.connectivity) }}
        </span>
      </div>
    </div>

    <p v-else>{{ t('settings.network.loading') }}</p>

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
    </div>
    <p v-if="forgetError" class="pill warn">{{ forgetError }}</p>
  </div>
</template>

<style scoped>
h1 { margin-top: 0; }
.status-card {
  padding: 1.5rem;
  max-width: 28rem;
  margin-bottom: 2rem;
}
.row {
  display: flex;
  justify-content: space-between;
  padding: 0.4rem 0;
}
.row + .row { border-top: 1px solid var(--border); }
.label { color: var(--text-dim); }
.actions {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
}
.action {
  padding: 1rem 1.5rem;
  font-size: 1.05rem;
}
.action:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
