<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api.js'

const checking = ref(false)
const checkError = ref(null)
const checkResult = ref(null)

async function loadLastCheck() {
  try {
    const data = await api.updateCheckStatus()
    checkResult.value = data.result
  } catch {
    // no cached result yet — leave blank rather than erroring out the page
  }
}

onMounted(loadLastCheck)

async function checkForUpdate() {
  checking.value = true
  checkError.value = null
  try {
    const data = await api.triggerUpdateCheck()
    checkResult.value = data.result
  } catch (err) {
    checkError.value = err.message
  } finally {
    checking.value = false
  }
}

const confirmingReboot = ref(false)
const rebooting = ref(false)
const rebootError = ref(null)

async function reboot() {
  rebooting.value = true
  rebootError.value = null
  try {
    await api.reboot()
  } catch (err) {
    // The device may drop the connection mid-reboot before the response
    // even lands — that's the expected outcome here, not a failure.
  } finally {
    confirmingReboot.value = false
  }
}

const confirmingUnpair = ref(false)
const unpairing = ref(false)
const unpairError = ref(null)

async function unpair() {
  unpairing.value = true
  unpairError.value = null
  try {
    await api.unpair()
    await reboot()
  } catch (err) {
    unpairError.value = err.message
    unpairing.value = false
  } finally {
    confirmingUnpair.value = false
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
    // Same as reboot() above — the device reboots as part of this, so a
    // dropped connection here is the expected outcome, not a failure.
  } finally {
    confirmingReset.value = false
    resetConfirmText.value = ''
  }
}
</script>

<template>
  <div>
    <h1>System</h1>

    <section class="block">
      <h2>Software Update</h2>
      <p class="hint">
        Checks the AnnouncementSlides server for a newer OS image. Reports
        "not paired" until this device has been paired (see "Pair This
        Device" on the home screen).
      </p>
      <button class="tile action" :disabled="checking" @click="checkForUpdate">
        {{ checking ? 'Checking…' : 'Check for Update' }}
      </button>

      <p v-if="checkError" class="pill warn">{{ checkError }}</p>

      <div v-if="checkResult" class="result tile">
        <div class="row">
          <span class="label">Exit code</span>
          <span :class="checkResult.exit_code === 0 ? 'pill ok' : 'pill warn'">
            {{ checkResult.exit_code }}
          </span>
        </div>
        <pre class="output">{{ checkResult.output }}</pre>
      </div>
    </section>

    <section class="block">
      <h2>Restart Device</h2>
      <p class="hint">Reboots the device immediately — the kiosk display will go dark for a bit.</p>

      <div v-if="rebooting" class="status-block">
        <p class="pill warn">Rebooting…</p>
      </div>
      <div v-else-if="!confirmingReboot" class="actions">
        <button class="tile action" @click="confirmingReboot = true">Restart Device</button>
      </div>
      <div v-else class="actions">
        <button class="tile action danger" @click="reboot">Yes, restart now</button>
        <button class="tile action" @click="confirmingReboot = false">Cancel</button>
      </div>
      <p v-if="rebootError" class="pill warn">{{ rebootError }}</p>
    </section>

    <section class="block">
      <h2>Unpair Device</h2>
      <p class="hint">
        Disconnects this device from its site and reboots to the pairing
        screen. Re-pairing (to this site or a different one) needs a fresh
        code from the website. Lighter than Factory Reset below — this
        leaves WiFi credentials and device identity alone.
      </p>

      <div v-if="unpairing" class="status-block">
        <p class="pill warn">Unpairing…</p>
      </div>
      <div v-else-if="!confirmingUnpair" class="actions">
        <button class="tile action danger" @click="confirmingUnpair = true">Unpair Device</button>
      </div>
      <div v-else class="actions">
        <button class="tile action danger" @click="unpair">Yes, unpair now</button>
        <button class="tile action" @click="confirmingUnpair = false">Cancel</button>
      </div>
      <p v-if="unpairError" class="pill warn">{{ unpairError }}</p>
    </section>

    <section class="block">
      <h2>Factory Reset</h2>
      <p class="hint">
        Wipes WiFi credentials, pairing, cached slides, and device identity,
        then reboots — the device comes back up exactly as it would on a
        brand-new SD card. This cannot be undone.
      </p>

      <div v-if="resetting" class="status-block">
        <p class="pill warn">Resetting…</p>
      </div>
      <div v-else-if="!confirmingReset" class="actions">
        <button class="tile action danger" @click="confirmingReset = true">Factory Reset</button>
      </div>
      <div v-else class="confirm-form">
        <label class="field">
          <span>Type RESET to confirm</span>
          <input type="text" v-model="resetConfirmText" autofocus autocomplete="off">
        </label>
        <div class="actions">
          <button
            class="tile action danger"
            :disabled="resetConfirmText !== 'RESET'"
            @click="factoryReset"
          >
            Erase Everything and Reset
          </button>
          <button class="tile action" @click="confirmingReset = false; resetConfirmText = ''">Cancel</button>
        </div>
      </div>
      <p v-if="resetError" class="pill warn">{{ resetError }}</p>
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
.action.danger {
  border-color: var(--danger);
  color: var(--danger);
}
.actions {
  display: flex;
  gap: 1rem;
}
.result {
  margin-top: 1rem;
  padding: 1rem 1.25rem;
}
.row {
  display: flex;
  justify-content: space-between;
  margin-bottom: 0.5rem;
}
.label { color: var(--text-dim); }
.output {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 0.85rem;
  color: #c7d1dd;
  max-height: 16rem;
  overflow-y: auto;
  margin: 0;
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
</style>
