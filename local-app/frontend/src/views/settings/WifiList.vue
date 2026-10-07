<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const networkBase = route.meta.networkBase || '/settings/network'
const accessPoints = ref([])
const loading = ref(true)
const error = ref(null)

async function scan() {
  loading.value = true
  error.value = null
  try {
    const data = await api.networkScan()
    accessPoints.value = data.access_points
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

onMounted(scan)

function select(ap) {
  router.push({
    path: `${networkBase}/wifi/${encodeURIComponent(ap.ssid)}`,
    query: { secured: ap.needs_password ? '1' : '0', ...(ap.supported ? {} : { unsupported: ap.kind }) },
  })
}
</script>

<template>
  <div>
    <h1>{{ t('settings.wifiList.title') }}</h1>

    <div class="toolbar">
      <button class="tile" @click="scan" :disabled="loading">
        {{ loading ? t('settings.wifiList.scanning') : t('settings.wifiList.rescan') }}
      </button>
    </div>

    <p v-if="error" class="pill warn">{{ error }}</p>
    <p v-else-if="loading && !accessPoints.length">{{ t('settings.wifiList.scanningForNetworks') }}</p>
    <p v-else-if="!accessPoints.length">{{ t('settings.wifiList.noNetworksFound') }}</p>

    <ul v-else class="ap-list">
      <li
        v-for="ap in accessPoints"
        :key="ap.ssid"
        tabindex="0"
        class="list-item"
        @click="select(ap)"
        @keydown.enter="select(ap)"
      >
        <span class="ssid">{{ ap.ssid }}</span>
        <span class="meta">
          <span v-if="ap.in_use" class="pill ok">{{ t('settings.wifiList.connected') }}</span>
          <span v-if="ap.kind === 'wpa3' || ap.kind === 'wpa2_wpa3' || !ap.supported" class="pill" :class="{ warn: !ap.supported }">
            {{ t(`settings.wifiList.kind.${ap.kind}`) }}
          </span>
          <span v-if="ap.needs_password">🔒</span>
          {{ ap.signal }}%
        </span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
h1 { margin-top: 0; }
.toolbar { margin-bottom: 1rem; }
.toolbar button { padding: 0.6rem 1.2rem; }
.ap-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  max-width: 32rem;
}
.list-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 1.2rem;
  font-size: 1.05rem;
}
.meta {
  color: var(--text-dim);
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
</style>
