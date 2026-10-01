<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { setSetupRequired } from '../../setupState.js'
import WizardNav from './WizardNav.vue'

const router = useRouter()
const { t } = useI18n()

const status = ref(null)
const starting = ref(false)
const restarting = ref(false)
const error = ref(null)

onMounted(async () => {
  status.value = await api.localStatus().catch(() => null)
})

// Marks setup finished, then either goes straight to the slideshow or —
// when a new device name is waiting on a hostname change (firstboot.py's
// set_hostname() only applies it at boot) — restarts once, which lands on
// the slideshow anyway.
async function start() {
  starting.value = true
  error.value = null
  try {
    await api.completeSetup()
  } catch (err) {
    error.value = err.message
    starting.value = false
    return
  }
  setSetupRequired(false)

  if (!status.value?.hostname_change_pending) {
    router.push('/kiosk')
    return
  }
  restarting.value = true
  try {
    await api.reboot()
  } catch (err) {
    // A dropped connection (TypeError) means it really is rebooting — see
    // Pairing.vue's rebootNow(). Anything else: just go to the slideshow;
    // the new name applies on the next restart.
    if (!(err instanceof TypeError)) router.push('/kiosk')
  }
}
</script>

<template>
  <div class="step">
    <div class="mark" aria-hidden="true">✓</div>
    <h1>{{ status?.paired ? t('setup.done.titlePaired') : t('setup.done.titleUnpaired') }}</h1>

    <template v-if="status?.paired">
      <p class="detail">
        {{ status.entity_name ? t('setup.pairing.paired', { entity: status.entity_name }) : t('setup.pairing.pairedNoEntity') }}
      </p>
      <p class="detail">{{ t('setup.done.slidesNote') }}</p>
    </template>
    <p v-else-if="status" class="detail">{{ t('setup.done.unpairedNote') }}</p>
    <p v-if="status?.hostname_change_pending" class="detail">{{ t('setup.done.restartNote') }}</p>
    <p v-if="error" class="pill warn">{{ error }}</p>

    <WizardNav
      back="/setup/pairing"
      :next-label="restarting ? t('setup.done.restarting') : t('setup.done.start')"
      :next-disabled="starting"
      autofocus-next
      @next="start"
    />
  </div>
</template>

<style scoped>
.step { max-width: 44rem; }
.mark {
  display: grid;
  place-items: center;
  width: 5rem;
  height: 5rem;
  border-radius: 50%;
  background: rgba(76, 175, 80, 0.15);
  border: var(--line-thick) solid var(--ok);
  color: var(--ok);
  font-size: 2.8rem;
  font-weight: 700;
  line-height: 1;
  margin-bottom: 1rem;
}
h1 { margin-top: 0; }
.detail { color: var(--text-dim); font-size: 1.1rem; }
</style>
