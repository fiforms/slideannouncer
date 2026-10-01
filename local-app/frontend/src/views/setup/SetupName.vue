<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import WizardNav from './WizardNav.vue'

const router = useRouter()
const { t } = useI18n()

const status = ref(null)
const loaded = ref(false)
const name = ref('')
const saving = ref(false)
const error = ref(null)

onMounted(async () => {
  try {
    status.value = await api.localStatus()
  } catch {
    // name stays blank; the backend falls back to the default hostname
  }
  // A name already given here (Back from Pairing) or else the current
  // hostname — so just pressing Next keeps the default.
  name.value = status.value?.device_name || status.value?.hostname || ''
  loaded.value = true
})

// Mirrors pairing.py's slugify_hostname(), just for the preview — the
// backend does the real derivation (and the pairing server may still add
// a "-2" suffix if another device on the same church already has it).
function slugifyHostname(value) {
  return value.trim().toLowerCase()
    .replace(/\s+/g, '_')
    .replace(/[^a-z0-9_-]/g, '')
    .replace(/^[_-]+|[_-]+$/g, '')
    .slice(0, 40)
    .replace(/^[_-]+|[_-]+$/g, '')
}

const hostnamePreview = computed(() => `${slugifyHostname(name.value) || status.value?.hostname || 'slideannouncer'}.local`)

async function next() {
  // Once paired the server owns the name (see main.py's /device-name).
  if (!status.value?.paired) {
    saving.value = true
    error.value = null
    try {
      await api.setDeviceName(name.value)
    } catch (err) {
      error.value = err.message
      return
    } finally {
      saving.value = false
    }
  }
  router.push('/setup/pairing')
}
</script>

<template>
  <div class="setup-card">
    <h1>{{ t('setup.name.title') }}</h1>

    <p v-if="status?.paired" class="intro">{{ t('setup.name.paired', { name: status.device_name || status.hostname }) }}</p>
    <template v-else>
      <p class="intro">{{ t('setup.name.intro') }}</p>
      <label class="field">
        <span class="field-label">{{ t('setup.name.label') }}</span>
        <input
          v-model="name"
          type="text"
          autocomplete="off"
          :disabled="saving"
          @keydown.enter.prevent="next"
        >
      </label>
      <p class="hint">{{ t('setup.name.hostname', { hostname: hostnamePreview }) }}</p>
    </template>
    <p v-if="error" class="pill warn">{{ error }}</p>

    <!-- Next, not the name field, gets focus: keeping the default is the
         common case, and Down/Up still reaches the field. -->
    <WizardNav back="/setup/network" :next-disabled="saving || !loaded" autofocus-next @next="next" />
  </div>
</template>

<style scoped>
.field {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.field-label { font-weight: 600; font-size: 1.1rem; }
.field input { width: 100%; }
.hint { margin: 0.75rem 0 0; color: var(--text-dim); overflow-wrap: anywhere; }
.pill { margin-top: 1rem; }
</style>
