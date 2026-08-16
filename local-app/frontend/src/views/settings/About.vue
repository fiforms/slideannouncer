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
      <dt>App version</dt><dd>{{ status.app_version ?? '—' }}</dd>
      <dt>Device UUID</dt><dd>{{ status.device_uuid ?? '—' }}</dd>
      <dt>Paired</dt><dd>{{ status.paired ? 'Yes' : 'No' }}</dd>
      <dt>Last heartbeat</dt>
      <dd>{{ status.heartbeat?.last_success_at ?? 'Never' }}</dd>
      <dt v-if="status.heartbeat?.last_error">Last heartbeat error</dt>
      <dd v-if="status.heartbeat?.last_error">{{ status.heartbeat.last_error }}</dd>
    </dl>
    <p v-if="!status?.paired" class="hint">
      Not paired yet — see "Pair This Device" on the home screen.
    </p>
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
