<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api.js'

const router = useRouter()
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
    path: `/settings/network/wifi/${encodeURIComponent(ap.ssid)}`,
    query: { secured: ap.security ? '1' : '0' },
  })
}
</script>

<template>
  <div>
    <h1>Set Up Wi-Fi</h1>

    <div class="toolbar">
      <button class="tile" @click="scan" :disabled="loading">
        {{ loading ? 'Scanning…' : 'Rescan' }}
      </button>
    </div>

    <p v-if="error" class="pill warn">{{ error }}</p>
    <p v-else-if="loading && !accessPoints.length">Scanning for networks…</p>
    <p v-else-if="!accessPoints.length">No networks found nearby.</p>

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
          <span v-if="ap.in_use" class="pill ok">Connected</span>
          <span v-if="ap.security">🔒</span>
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
