<script setup>
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'

const { t } = useI18n()

// Safety word for the factory-reset confirmation input — deliberately not
// translated (an arbitrary token to type, not a sentence), same idea as
// leaving raw version numbers/IPs untranslated elsewhere in this app.
const RESET_CONFIRM_WORD = 'RESET'

const confirmingReboot = ref(false)
const rebooting = ref(false)
const rebootError = ref(null)

const sleeping = ref(false)
const sleepError = ref(null)

async function sleepDisplay() {
  sleeping.value = true
  sleepError.value = null
  try {
    await api.sleepDisplay()
  } catch (err) {
    // Same reasoning as reboot()'s catch below: a TypeError here means
    // the kiosk — the very browser rendering this page — already went
    // down as intended (systemctl stop slide-announcer-kiosk.service),
    // not a real failure. Nothing un-sets `sleeping` on that path since
    // there's no page left to update; it only matters on a genuine error.
    if (!(err instanceof TypeError)) {
      sleepError.value = err.message
      sleeping.value = false
    }
  }
}

async function reboot() {
  rebooting.value = true
  rebootError.value = null
  try {
    await api.reboot()
  } catch (err) {
    // fetch() itself throws TypeError for a genuine network-level failure
    // (connection dropped because the device is actually rebooting) —
    // expected, not a failure. api.js's request() throws a plain Error for
    // anything else (an HTTP error response the backend actually sent,
    // e.g. systemctl reboot rejected by polkit) — that must be surfaced,
    // not silently treated as "rebooting" when the device never actually
    // will.
    if (!(err instanceof TypeError)) {
      rebootError.value = err.message
      rebooting.value = false
    }
  } finally {
    confirmingReboot.value = false
  }
}

const confirmingReset = ref(false)
const resetConfirmText = ref('')
const resetting = ref(false)
const resetError = ref(null)

async function factoryReset() {
  resetting.value = true
  resetError.value = null
  try {
    await api.factoryReset()
  } catch (err) {
    // Same as reboot() above: fetch()'s own TypeError means the device
    // actually dropped the connection rebooting (expected), but anything
    // else is a real backend-reported failure and must be surfaced, not
    // silently treated as "resetting" when the device never actually will.
    if (!(err instanceof TypeError)) {
      resetError.value = err.message
      resetting.value = false
    }
  } finally {
    confirmingReset.value = false
    resetConfirmText.value = ''
  }
}
</script>

<template>
  <!-- Same layout as System.vue (which links here): no page title, one
       .panel box per section with a .panel-title heading bar, compact
       enough to fit the screen without scrolling. -->
  <div class="settings-page">
    <section class="tile panel">
      <div class="panel-title"><h2>{{ t('settings.deviceTools.restartTitle') }}</h2></div>
      <p class="hint">{{ t('settings.deviceTools.restartHint') }}</p>

      <p v-if="rebooting" class="pill warn">{{ t('settings.deviceTools.rebooting') }}</p>
      <div v-else-if="!confirmingReboot" class="actions">
        <button class="tile action" @click="confirmingReboot = true">{{ t('settings.deviceTools.restartButton') }}</button>
        <button class="tile action" :disabled="sleeping" @click="sleepDisplay">
          {{ sleeping ? t('settings.deviceTools.sleeping') : t('settings.deviceTools.sleepButton') }}
        </button>
      </div>
      <div v-else class="actions">
        <button class="tile action danger" @click="reboot">{{ t('settings.deviceTools.restartConfirm') }}</button>
        <button class="tile action" @click="confirmingReboot = false">{{ t('common.cancel') }}</button>
      </div>
      <p v-if="rebootError" class="pill warn note">{{ rebootError }}</p>
      <p v-if="sleepError" class="pill warn note">{{ sleepError }}</p>
      <p class="hint note">{{ t('settings.deviceTools.sleepHint') }}</p>
    </section>

    <section class="tile panel">
      <div class="panel-title"><h2>{{ t('settings.deviceTools.factoryResetTitle') }}</h2></div>
      <p class="hint">{{ t('settings.deviceTools.factoryResetHint') }}</p>

      <p v-if="resetting" class="pill warn">{{ t('settings.deviceTools.resetting') }}</p>
      <div v-else-if="!confirmingReset" class="actions">
        <button class="tile action danger" @click="confirmingReset = true">{{ t('settings.deviceTools.factoryResetButton') }}</button>
      </div>
      <!-- Confirm word field and its buttons on one row, so the confirm
           step doesn't grow the box (and push the page into scrolling). -->
      <div v-else class="confirm-form">
        <label class="field">
          <span class="label">{{ t('settings.deviceTools.typeToConfirm', { word: RESET_CONFIRM_WORD }) }}</span>
          <input type="text" v-model="resetConfirmText" autofocus autocomplete="off">
        </label>
        <div class="actions">
          <button
            class="tile action danger"
            :disabled="resetConfirmText !== RESET_CONFIRM_WORD"
            @click="factoryReset"
          >
            {{ t('settings.deviceTools.eraseButton') }}
          </button>
          <button class="tile action" @click="confirmingReset = false; resetConfirmText = ''">{{ t('common.cancel') }}</button>
        </div>
      </div>
      <p v-if="resetError" class="pill warn note">{{ resetError }}</p>
    </section>

    <section class="tile panel">
      <div class="panel-title"><h2>{{ t('settings.deviceTools.keyDebugTitle') }}</h2></div>
      <div class="inline-row">
        <p class="hint">{{ t('settings.deviceTools.keyDebugHint') }}</p>
        <router-link to="/settings/keydebug" class="tile action key-debug-link">{{ t('settings.deviceTools.openKeyDebug') }}</router-link>
      </div>
    </section>
  </div>
</template>

<style scoped>
.hint {
  color: var(--text-dim);
  margin: 0 0 0.75rem;
  font-size: 0.9rem;
}
.note {
  margin: 0.6rem 0 0;
}
.label {
  color: var(--text-dim);
  font-weight: 600;
}
.action {
  padding: 0.75rem 1.5rem;
  font-size: 1rem;
}
.action.danger {
  border-color: var(--danger);
  color: var(--danger);
}
.actions {
  display: flex;
  gap: 1rem;
  margin-top: 0.35rem;
}
.confirm-form {
  display: flex;
  align-items: flex-end;
  flex-wrap: wrap;
  gap: 0.75rem;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.field input {
  padding: 0.55rem 0.9rem;
  font-size: 1rem;
}
.inline-row {
  display: flex;
  align-items: center;
  gap: 1.5rem;
}
.inline-row .hint {
  flex: 1;
  margin: 0;
}
.key-debug-link {
  flex: none;
  text-decoration: none;
}
</style>
