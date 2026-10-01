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
//
// Already online either way (WiFi, or Back to here over a cable)? Then
// Next is the likely press, so it gets the remote's focus instead of the
// WiFi setup button.
const checking = ref(!!route.query.auto)
const online = ref(false)

onMounted(async () => {
  try {
    const status = await api.networkStatus()
    online.value = status.connected && status.connectivity === 'full'
    if (checking.value && online.value && status.connection_type === 'ethernet') {
      router.replace('/setup/name')
      return
    }
  } catch {
    // fall through to the normal Network screen
  }
  if (checking.value) {
    router.replace({ path: route.path })
    checking.value = false
  }
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
      <WizardNav back="/setup" :autofocus-next="online" @next="router.push('/setup/name')" />
    </template>
  </div>
</template>

<style scoped>
h1 { margin-top: 0; }
.intro { color: var(--text-dim); font-size: 1.1rem; }
</style>
