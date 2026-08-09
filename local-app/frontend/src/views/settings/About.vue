<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api.js'

const status = ref(null)

onMounted(async () => {
  try {
    status.value = await api.localStatus()
  } catch {
    // status endpoint unreachable — leave fields blank rather than erroring
    // out an otherwise-informational screen
  }
})
</script>

<template>
  <div>
    <h1>About This Device</h1>
    <dl v-if="status">
      <dt>Hostname</dt><dd>{{ status.hostname ?? '—' }}</dd>
      <dt>Image version</dt><dd>{{ status.image_version ?? '—' }}</dd>
      <dt>Device UUID</dt><dd>{{ status.device_uuid ?? '—' }}</dd>
    </dl>
    <p class="hint">Pairing and unpair status will appear here once device pairing is implemented.</p>
  </div>
</template>

<style scoped>
h1 { margin-top: 0; }
dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.4rem 1.5rem;
  max-width: 28rem;
  margin-bottom: 1.5rem;
}
dt { color: var(--text-dim); }
.hint { color: var(--text-dim); }
</style>
