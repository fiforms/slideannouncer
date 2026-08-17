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
  <div>
    <h1>{{ t('settings.deviceTools.title') }}</h1>

    <section class="block">
      <h2>{{ t('settings.deviceTools.restartTitle') }}</h2>
      <p class="hint">{{ t('settings.deviceTools.restartHint') }}</p>

      <div v-if="rebooting" class="status-block">
        <p class="pill warn">{{ t('settings.deviceTools.rebooting') }}</p>
      </div>
      <div v-else-if="!confirmingReboot" class="actions">
        <button class="tile action" @click="confirmingReboot = true">{{ t('settings.deviceTools.restartButton') }}</button>
      </div>
      <div v-else class="actions">
        <button class="tile action danger" @click="reboot">{{ t('settings.deviceTools.restartConfirm') }}</button>
        <button class="tile action" @click="confirmingReboot = false">{{ t('common.cancel') }}</button>
      </div>
      <p v-if="rebootError" class="pill warn">{{ rebootError }}</p>
    </section>

    <section class="block">
      <h2>{{ t('settings.deviceTools.factoryResetTitle') }}</h2>
      <p class="hint">
        {{ t('settings.deviceTools.factoryResetHint') }}
      </p>

      <div v-if="resetting" class="status-block">
        <p class="pill warn">{{ t('settings.deviceTools.resetting') }}</p>
      </div>
      <div v-else-if="!confirmingReset" class="actions">
        <button class="tile action danger" @click="confirmingReset = true">{{ t('settings.deviceTools.factoryResetButton') }}</button>
      </div>
      <div v-else class="confirm-form">
        <label class="field">
          <span>{{ t('settings.deviceTools.typeToConfirm', { word: RESET_CONFIRM_WORD }) }}</span>
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
      <p v-if="resetError" class="pill warn">{{ resetError }}</p>
    </section>

    <section class="block">
      <h2>{{ t('settings.deviceTools.keyDebugTitle') }}</h2>
      <p class="hint">{{ t('settings.deviceTools.keyDebugHint') }}</p>
      <router-link to="/settings/keydebug" class="tile action key-debug-link">{{ t('settings.deviceTools.openKeyDebug') }}</router-link>
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
.action.danger {
  border-color: var(--danger);
  color: var(--danger);
}
.actions {
  display: flex;
  gap: 1rem;
}
.status-block { margin-top: 0.5rem; }
.confirm-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  align-items: flex-start;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.key-debug-link {
  display: inline-block;
  text-decoration: none;
}
</style>
