<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api.js'

const router = useRouter()
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
</script>

<template>
  <div>
    <h1>Network</h1>

    <p v-if="error" class="pill warn">{{ error }}</p>

    <div v-else-if="status" class="status-card tile">
      <div class="row">
        <span class="label">Connection</span>
        <span class="pill" :class="status.connected ? 'ok' : 'warn'">
          {{ status.connected ? status.connection_type : 'Disconnected' }}
        </span>
      </div>
      <div v-if="status.ssid" class="row">
        <span class="label">Network name</span>
        <span>{{ status.ssid }}</span>
      </div>
      <div v-if="status.signal !== null && status.signal !== undefined" class="row">
        <span class="label">Signal</span>
        <span>{{ status.signal }}%</span>
      </div>
      <div class="row">
        <span class="label">IP address</span>
        <span>{{ status.ip_addresses?.length ? status.ip_addresses.join(', ') : '—' }}</span>
      </div>
    </div>

    <p v-else>Loading network status…</p>

    <div class="actions">
      <button class="tile action" @click="goToWifiSetup">Set Up Wi-Fi</button>
      <button
        v-if="status?.connection_type === 'wifi' && status?.connected"
        class="tile action"
        :disabled="forgetting"
        @click="forgetNetwork"
      >
        {{ forgetting ? 'Forgetting…' : 'Forget This Network' }}
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
