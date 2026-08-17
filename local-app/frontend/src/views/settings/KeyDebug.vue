<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

// Capture phase + no preventDefault/stopPropagation: this must see every
// key exactly as it arrives at the page, before remoteNav.js's own
// bubble-phase listener can act on or swallow it, so what's shown here is
// true even for keys remoteNav.js already handles.
const events = ref([])

function onKeydown(event) {
  events.value = [
    {
      time: new Date().toLocaleTimeString(undefined, { hour12: false }) + '.' + String(event.timeStamp | 0).slice(-3),
      key: event.key,
      code: event.code,
      keyCode: event.keyCode,
      which: event.which,
      repeat: event.repeat,
    },
    ...events.value,
  ].slice(0, 25)
}

onMounted(() => window.addEventListener('keydown', onKeydown, { capture: true }))
onUnmounted(() => window.removeEventListener('keydown', onKeydown, { capture: true }))
</script>

<template>
  <div>
    <h1>{{ t('settings.keyDebug.title') }}</h1>
    <i18n-t keypath="settings.keyDebug.hint" tag="p" class="hint">
      <template #code><code>keydown</code></template>
    </i18n-t>
    <table v-if="events.length">
      <thead>
        <tr>
          <th>{{ t('settings.keyDebug.time') }}</th>
          <th>key</th>
          <th>code</th>
          <th>keyCode</th>
          <th>which</th>
          <th>repeat</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(e, i) in events" :key="i">
          <td>{{ e.time }}</td>
          <td>{{ e.key }}</td>
          <td>{{ e.code }}</td>
          <td>{{ e.keyCode }}</td>
          <td>{{ e.which }}</td>
          <td>{{ e.repeat ? t('common.yes') : '' }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else class="hint">{{ t('settings.keyDebug.waitingForKeypress') }}</p>
  </div>
</template>

<style scoped>
h1 { margin-top: 0; }
.hint { color: var(--text-dim); max-width: 34rem; }
table {
  border-collapse: collapse;
  font: 0.95rem/1.4 monospace;
}
th, td {
  padding: 0.3rem 0.9rem 0.3rem 0;
  text-align: left;
  border-bottom: 1px solid var(--panel-hover);
}
th { color: var(--text-dim); font-weight: 600; }
</style>
