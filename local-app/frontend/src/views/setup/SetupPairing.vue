<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import PairingForm from '../../components/PairingForm.vue'
import WizardNav from './WizardNav.vue'

const router = useRouter()
const { t } = useI18n()

const status = ref(null)
const loaded = ref(false)

onMounted(async () => {
  try {
    status.value = await api.localStatus()
  } catch {
    // show the form anyway — pairing itself will report a real failure
  }
  loaded.value = true
})

function next() {
  router.push('/setup/done')
}
</script>

<template>
  <div>
    <h1>{{ t('setup.pairing.title') }}</h1>

    <template v-if="loaded">
      <p v-if="status?.paired" class="intro">
        {{ status.entity_name ? t('setup.pairing.paired', { entity: status.entity_name }) : t('setup.pairing.pairedNoEntity') }}
      </p>
      <!-- Same form as Settings > Pairing, minus its name field: the name
           was already chosen on the previous step. -->
      <PairingForm
        v-else
        :server-url="status?.server_url"
        :default-name="status?.device_name || status?.hostname"
        :show-name-field="false"
        @paired="next"
      />
    </template>

    <WizardNav
      back="/setup/name"
      :next-label="status?.paired ? null : t('setup.skip')"
      :next-disabled="!loaded"
      @next="next"
    />
  </div>
</template>

<style scoped>
h1 { margin-top: 0; }
.intro { color: var(--text-dim); font-size: 1.1rem; }
</style>
