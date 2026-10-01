<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { setLocale, LANGUAGE_OPTIONS } from '../../i18n.js'
import Dropdown from '../../components/Dropdown.vue'
import WizardNav from './WizardNav.vue'

const router = useRouter()
const { t, locale } = useI18n()

const saving = ref(false)
const error = ref(null)

// Switches the wizard's own language on the spot, and saves it on the
// device (pairing.py's LOCAL_LANGUAGE_FILE) so it sticks across reboots
// and is offered to the server as this device's language at pairing.
async function chooseLanguage(code) {
  setLocale(code)
  saving.value = true
  error.value = null
  try {
    await api.setLanguage(code)
  } catch (err) {
    error.value = err.message
  } finally {
    saving.value = false
  }
}

async function next() {
  // Save the shown language even if the dropdown was never touched (the
  // boot-yaml hint's language was already right). A failed save isn't
  // worth stopping setup over — the screen is already in that language.
  await chooseLanguage(locale.value)
  // ?auto: SetupNetwork skips itself when a cable is already online —
  // only on the way forward, so Back from the next step still shows it.
  router.push({ path: '/setup/network', query: { auto: '1' } })
}
</script>

<template>
  <div class="step">
    <h1>{{ t('setup.welcome.title') }}</h1>
    <p class="intro">{{ t('setup.welcome.intro') }}</p>

    <div class="field">
      <span class="field-label">{{ t('setup.welcome.languageLabel') }}</span>
      <Dropdown
        :model-value="locale"
        :options="LANGUAGE_OPTIONS"
        :disabled="saving"
        @update:model-value="chooseLanguage"
      />
    </div>
    <p v-if="error" class="pill warn">{{ error }}</p>

    <WizardNav :next-disabled="saving" @next="next" />
  </div>
</template>

<style scoped>
.step { max-width: 40rem; }
h1 { margin-top: 0; font-size: 2.2rem; }
.intro { color: var(--text-dim); font-size: 1.15rem; margin-bottom: 2rem; }
.field {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  align-items: flex-start;
}
.field-label { font-weight: 600; }
</style>
