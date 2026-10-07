<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import Dropdown from '@core/components/Dropdown.vue'

const { t } = useI18n()

const discovered = ref([])
const scanning = ref(false)
const scanError = ref(null)

const peers = ref([])
const statusError = ref(null)

const pairingTarget = ref(null) // { host, port, name } while the PIN form is open
const pin = ref('')
const pairing = ref(false)
const pairError = ref(null)
const pairDialog = ref(null) // <dialog> ref — see startPairing()/onDialogClosed()

// Device-global — applies to whatever any paired master pushes, not
// per-peer. null means "leave Revelation's own ?variant=/?lang= alone".
const VARIANTS = ['normal', 'lowerthirds', 'confidence', 'notes', 'remotepreview', 'notesteleprompter']
const LANGUAGES = ['en', 'es']
const displayVariant = ref(null)
const displayLang = ref(null)
const displaySettingsSaving = ref(false)
const displaySettingsError = ref(null)

const variantOptions = computed(() => [
  { value: null, label: t('settings.revelation.default') },
  ...VARIANTS.map((value) => ({ value, label: t(`settings.revelation.variant_${value}`) })),
])
const languageOptions = computed(() => [
  { value: null, label: t('settings.revelation.default') },
  ...LANGUAGES.map((value) => ({ value, label: t(`settings.revelation.language_${value}`) })),
])

async function loadDisplaySettings() {
  try {
    const data = await api.revelationDisplaySettings()
    displayVariant.value = data.variant
    displayLang.value = data.lang
  } catch {
    // leave blank rather than erroring out the page
  }
}

async function selectDisplayVariant(value) {
  if (value === displayVariant.value || displaySettingsSaving.value) return
  displaySettingsSaving.value = true
  displaySettingsError.value = null
  try {
    const data = await api.setRevelationDisplaySettings(value, displayLang.value)
    displayVariant.value = data.variant
    displayLang.value = data.lang
  } catch (err) {
    displaySettingsError.value = err.message
  } finally {
    displaySettingsSaving.value = false
  }
}

async function selectDisplayLang(value) {
  if (value === displayLang.value || displaySettingsSaving.value) return
  displaySettingsSaving.value = true
  displaySettingsError.value = null
  try {
    const data = await api.setRevelationDisplaySettings(displayVariant.value, value)
    displayVariant.value = data.variant
    displayLang.value = data.lang
  } catch (err) {
    displaySettingsError.value = err.message
  } finally {
    displaySettingsSaving.value = false
  }
}

async function scan() {
  scanning.value = true
  scanError.value = null
  try {
    const data = await api.revelationScan()
    discovered.value = data.discovered
  } catch (err) {
    scanError.value = err.message
  } finally {
    scanning.value = false
  }
}

async function loadStatus() {
  try {
    const data = await api.revelationStatus()
    peers.value = data.peers
    statusError.value = null
  } catch (err) {
    statusError.value = err.message
  }
}

function startPairing(instance) {
  pairingTarget.value = { host: instance.host, port: instance.port, name: instance.hostname || instance.name }
  pin.value = ''
  pairError.value = null
  // showModal() (not just rendering the <dialog>) is what makes the rest of
  // the document — including the Settings left rail, a sibling component
  // this one has no reach into — inert for keyboard/remote navigation. A
  // plain :inert binding on this component's own markup was tried first
  // and confirmed on hardware not to reach that rail at all.
  nextTick(() => pairDialog.value?.showModal())
}

function cancelPairing() {
  pairDialog.value?.close()
}

// <dialog>'s native "close" event (fires for close(), Escape, or a
// successful pairing below) is the single place pairingTarget is actually
// cleared, so the heading/form don't blank out mid-close-animation.
function onDialogClosed() {
  pairingTarget.value = null
}

function onDialogClick(event) {
  // A click that lands on the ::backdrop (not any dialog content) reports
  // the dialog element itself as the target — this is the standard way to
  // detect a "click outside" dismiss for a native <dialog>.
  if (event.target === pairDialog.value) cancelPairing()
}

async function confirmPairing() {
  pairing.value = true
  pairError.value = null
  try {
    await api.revelationPair(pairingTarget.value.host, pairingTarget.value.port, pin.value)
    pairDialog.value?.close()
    await loadStatus()
  } catch (err) {
    pairError.value = err.message
  } finally {
    pairing.value = false
  }
}

async function unpair(instanceId) {
  await api.revelationUnpair(instanceId)
  await loadStatus()
}

function isAlreadyPaired(instanceId) {
  return peers.value.some((peer) => peer.instanceId === instanceId)
}

let statusInterval = null

onMounted(() => {
  scan()
  loadStatus()
  loadDisplaySettings()
  statusInterval = setInterval(loadStatus, 10000)
})
onUnmounted(() => clearInterval(statusInterval))
</script>

<template>
  <div class="settings-page">
    <section class="tile panel">
      <div class="panel-title"><h2>{{ t('settingsLayout.revelationPeering') }}</h2></div>
      <p class="hint">{{ t('settings.revelation.intro') }}</p>

      <!-- Device-global display overrides — Default leaves whatever
           ?variant=/?lang= Revelation itself sends untouched. -->
      <div class="selects">
        <div class="field">
          <span class="label">{{ t('settings.revelation.variantLabel') }}</span>
          <Dropdown
            :model-value="displayVariant"
            :options="variantOptions"
            :disabled="displaySettingsSaving"
            @update:model-value="selectDisplayVariant"
          />
        </div>
        <div class="field">
          <span class="label">{{ t('settings.revelation.languageLabel') }}</span>
          <Dropdown
            :model-value="displayLang"
            :options="languageOptions"
            :disabled="displaySettingsSaving"
            @update:model-value="selectDisplayLang"
          />
        </div>
      </div>
      <p v-if="displaySettingsError" class="pill warn note">{{ displaySettingsError }}</p>
    </section>

    <section class="tile panel">
      <div class="panel-title"><h2>{{ t('settings.revelation.pairedTitle') }}</h2></div>
      <p v-if="statusError" class="pill warn">{{ statusError }}</p>
      <p v-else-if="!peers.length" class="hint">{{ t('settings.revelation.noPeers') }}</p>
      <ul v-else class="peer-list">
        <li v-for="peer in peers" :key="peer.instanceId" class="list-item">
          <span class="peer-name">{{ peer.name }}</span>
          <span class="meta">
            <span v-if="peer.needsRepair" class="pill warn">
              {{ t('settings.revelation.needsRepair') }}
            </span>
            <span v-else class="pill" :class="peer.connection?.connected ? 'ok' : 'warn'">
              {{ peer.connection?.connected ? t('settings.revelation.connected') : t('settings.revelation.notConnected') }}
            </span>
            <button type="button" class="tile action" @click="unpair(peer.instanceId)">
              {{ t('settings.revelation.unpair') }}
            </button>
          </span>
        </li>
      </ul>
    </section>

    <section class="tile panel">
      <div class="panel-title">
        <h2>{{ t('settings.revelation.nearbyTitle') }}</h2>
        <button class="tile bar-button" :disabled="scanning" @click="scan">
          {{ scanning ? t('settings.revelation.scanning') : t('settings.revelation.rescan') }}
        </button>
      </div>

      <p v-if="scanError" class="pill warn">{{ scanError }}</p>
      <p v-else-if="scanning && !discovered.length" class="hint">{{ t('settings.revelation.scanning') }}</p>
      <p v-else-if="!discovered.length" class="hint">{{ t('settings.revelation.noneFound') }}</p>

      <ul v-else class="peer-list">
        <li
          v-for="instance in discovered"
          :key="instance.instanceId"
          tabindex="0"
          class="list-item"
          @click="!isAlreadyPaired(instance.instanceId) && startPairing(instance)"
          @keydown.enter="!isAlreadyPaired(instance.instanceId) && startPairing(instance)"
        >
          <span class="peer-name">{{ instance.hostname || instance.name }}</span>
          <span class="meta">
            <span v-if="isAlreadyPaired(instance.instanceId)" class="pill ok">{{ t('settings.revelation.paired') }}</span>
            <span v-else>{{ instance.host }}</span>
          </span>
        </li>
      </ul>
    </section>

    <dialog ref="pairDialog" class="pair-dialog" @click="onDialogClick" @close="onDialogClosed">
      <h2 v-if="pairingTarget">{{ t('settings.revelation.pairWith', { name: pairingTarget.name }) }}</h2>
      <form class="form" @submit.prevent="confirmPairing">
        <label class="field">
          <span>{{ t('settings.revelation.pin') }}</span>
          <input v-model="pin" type="text" inputmode="numeric" autofocus autocomplete="off">
        </label>
        <p v-if="pairError" class="pill warn">{{ pairError }}</p>
        <div class="button-row">
          <button type="button" class="tile" data-nav-close @click="cancelPairing">{{ t('common.cancel') }}</button>
          <button type="submit" class="tile action" :disabled="pairing || !pin">
            {{ pairing ? t('settings.revelation.pairing') : t('settings.revelation.pair') }}
          </button>
        </div>
      </form>
    </dialog>
  </div>
</template>

<style scoped>
.hint { color: var(--text-dim); margin: 0; }
.note { margin: 0.6rem 0 0; }
.selects {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem 2rem;
  margin-top: 0.9rem;
}
.label { color: var(--text-dim); font-weight: 600; }
.bar-button {
  padding: 0.3rem 0.9rem;
  font-size: 0.9rem;
}
.peer-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.list-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.6rem 1rem;
}
.peer-name { font-weight: 600; }
.field { display: flex; flex-direction: column; gap: 0.35rem; }
.meta {
  color: var(--text-dim);
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.action { padding: 0.45rem 1rem; font-size: 0.9rem; }
/* showModal() gives this real modal semantics (focus trapped inside,
   Escape closes it, everything else in the document made inert) — see
   startPairing()'s own comment for why a manual :inert binding couldn't
   reach far enough to do the same. */
.pair-dialog {
  /* A bare class selector like .pair-dialog outranks the browser's own
     `dialog:not([open]) { display: none }` default, so without this the
     dialog stayed laid out (visible, sitting in the page flow) even while
     closed — confirmed on hardware. Scoping display to [open] (the
     attribute showModal()/close() actually toggle) restores that default
     for the closed state instead of fighting it. */
  display: none;
  flex-direction: column;
  gap: 1rem;
  background: var(--panel, #1c1c1c);
  color: var(--text);
  padding: 2rem;
  border: none;
  border-radius: 0.8rem;
  min-width: 20rem;
}
.pair-dialog[open] {
  display: flex;
}
.pair-dialog::backdrop {
  background: rgba(0, 0, 0, 0.85);
}
.form { display: flex; flex-direction: column; gap: 1.25rem; }
.form .field { margin-bottom: 0; }
.button-row { display: flex; gap: 0.75rem; justify-content: flex-end; }
</style>
