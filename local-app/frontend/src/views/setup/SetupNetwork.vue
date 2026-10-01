<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import NetworkStatus from '../settings/NetworkStatus.vue'
import WizardNav from './WizardNav.vue'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

// Coming forward from Welcome (?auto=1) with a network cable already
// online, there's nothing to set up here — go straight on to naming.
// Anything short of that (WiFi, no internet, a captive portal, a failed
// check) shows the step as normal.
const checking = ref(!!route.query.auto)

onMounted(async () => {
  if (!checking.value) return
  try {
    const status = await api.networkStatus()
    if (status.connection_type === 'ethernet' && status.connectivity === 'full') {
      router.replace('/setup/name')
      return
    }
  } catch {
    // fall through to the normal Network screen
  }
  router.replace({ path: route.path })
  checking.value = false
})
</script>

<template>
  <div>
    <h1>{{ t('setup.network.title') }}</h1>
    <p v-if="checking" class="intro">{{ t('setup.network.checking') }}</p>
    <template v-else>
      <p class="intro">{{ t('setup.network.intro') }}</p>
      <!-- The same screen as Settings > Network; its WiFi setup button
           leads to /setup/network/wifi via meta.networkBase. -->
      <NetworkStatus />
      <WizardNav back="/setup" @next="router.push('/setup/name')" />
    </template>
  </div>
</template>

<style scoped>
h1 { margin-top: 0; }
.intro { color: var(--text-dim); font-size: 1.1rem; }
</style>
