<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const status = ref(null)
const error = ref(null)

onMounted(async () => {
  try {
    status.value = await api.localStatus()
  } catch (err) {
    error.value = err.message
  }
})
</script>

<template>
  <div class="card">
    <h1>Slide Announcer</h1>
    <p v-if="error" class="status">Could not reach the local backend: {{ error }}</p>
    <p v-else-if="status" class="status">{{ status.message }}</p>
    <p v-else class="status">Loading status…</p>

    <dl v-if="status">
      <dt>Hostname</dt><dd>{{ status.hostname ?? '—' }}</dd>
      <dt>Image version</dt><dd>{{ status.image_version ?? '—' }}</dd>
      <dt>App version</dt><dd>{{ status.app_version ?? '—' }}</dd>
      <dt>Device UUID</dt><dd>{{ status.device_uuid ?? '—' }}</dd>
      <dt>Setup mode detected</dt><dd>{{ status.setup_mode ?? '—' }}</dd>
      <dt>Paired</dt><dd>{{ status.paired ? 'Yes' : 'No' }}</dd>
    </dl>

    <p v-if="status?.paired" class="status">{{ status.sync?.message }}</p>

    <div class="links">
      <router-link v-if="status && !status.paired" to="/pairing" class="tile settings-link">
        Pair This Device
      </router-link>
      <router-link to="/settings" class="tile settings-link">Settings</router-link>
    </div>

    <router-link to="/kiosk" class="tile settings-link slideshow-link">
      &#8617; Slideshow
    </router-link>
  </div>
</template>

<style scoped>
.card {
  max-width: 32rem;
  margin: 10vh auto 0;
  text-align: center;
}
.links {
  display: flex;
  gap: 1rem;
  justify-content: center;
}
h1 { font-size: 2rem; margin-bottom: 0.5rem; }
.status { color: var(--text-dim); margin: 1rem 0; }
dl {
  text-align: left;
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.25rem 1rem;
  font-size: 0.9rem;
  color: #c7d1dd;
  margin-bottom: 2rem;
}
dt { font-weight: 600; color: var(--text-dim); }
.settings-link {
  display: inline-block;
  padding: 0.9rem 2.5rem;
  font-size: 1.1rem;
  font-weight: 600;
  text-decoration: none;
}
.slideshow-link {
  margin-top: 1rem;
}
</style>
