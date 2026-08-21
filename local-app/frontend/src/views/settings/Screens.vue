<script setup>
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'

const { t } = useI18n()

const resolution = ref(null)
const saving = ref(false)
const error = ref(null)

async function load() {
  try {
    const data = await api.screenResolutionStatus()
    resolution.value = data.screen_resolution
  } catch {
    // leave blank rather than erroring out the page
  }
}

async function select(value) {
  if (value === resolution.value || saving.value) return
  saving.value = true
  error.value = null
  try {
    const data = await api.setScreenResolution(value)
    resolution.value = data.screen_resolution
  } catch (err) {
    error.value = err.message
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h1>{{ t('settings.screens.title') }}</h1>

    <section class="block">
      <div class="tile result">
        <h2>{{ t('settings.screens.resolution') }}</h2>
        <div class="actions">
          <button
            class="tile action"
            :class="{ active: resolution === '4k' }"
            :disabled="saving"
            @click="select('4k')"
          >
            {{ t('settings.screens.resolution4k') }}
          </button>
          <button
            class="tile action"
            :class="{ active: resolution === '1080p' }"
            :disabled="saving"
            @click="select('1080p')"
          >
            {{ t('settings.screens.resolution1080p') }}
          </button>
        </div>
        <p v-if="error" class="pill warn">{{ error }}</p>
      </div>
    </section>

    <section class="block">
      <h2>{{ t('settings.screens.multiMonitor') }}</h2>
      <p class="hint">{{ t('settings.screens.multiMonitorHint') }}</p>
    </section>
  </div>
</template>

<style scoped>
h1 { margin-top: 0; }
.block {
  max-width: 32rem;
  margin-bottom: 2.5rem;
}
h2 {
  font-size: 1.1rem;
  margin-bottom: 0.5rem;
}
.hint {
  color: var(--text-dim);
  margin-top: 0;
}
.action {
  padding: 0.9rem 1.6rem;
  font-size: 1.05rem;
}
.action:disabled { opacity: 0.6; cursor: default; }
.action.active {
  border-color: var(--accent, #6c8cff);
  color: var(--accent, #6c8cff);
}
.actions {
  display: flex;
  gap: 1rem;
}
.result {
  padding: 1.25rem 1.5rem;
}
.result h2 { margin-top: 0; }
</style>
