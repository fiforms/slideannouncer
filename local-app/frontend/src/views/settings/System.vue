<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../../api.js'

const checking = ref(false)
const checkError = ref(null)
const checkResult = ref(null)
const versions = ref({ image_version: null, app_version: null })

const applyError = ref(null)
const updateRunning = ref(false)
const progress = ref(null)
let progressTimer = null

// checkResult.data is the structured heartbeat response update-check.py
// pulls out of the CLI's stdout (see that script's _leading_json) — null
// when the device isn't paired yet (the CLI exits with a plain-text error
// instead of JSON in that case), so every field below is optional-chained.
const updateInfo = computed(() => checkResult.value?.data ?? null)

const osUpdate = computed(() => {
  const info = updateInfo.value
  if (!info?.os_update_available) return null
  return { version: info.latest_os_version, releaseType: info.os_release_type }
})

const appUpdate = computed(() => {
  const info = updateInfo.value
  if (!info?.app_update_available) return null
  return { version: info.latest_app_version }
})

// Same priority the backend's trigger_update_apply() applies: an OS
// update (hotfix or full) goes first, then the local-app update — never
// both from one click.
const nextUpdate = computed(() => {
  if (osUpdate.value) return { kind: 'os', ...osUpdate.value }
  if (appUpdate.value) return { kind: 'app', ...appUpdate.value }
  return null
})

async function loadVersions() {
  try {
    const status = await api.localStatus()
    versions.value = { image_version: status.image_version, app_version: status.app_version }
  } catch {
    // leave blank rather than erroring out the page
  }
}

async function loadLastCheck() {
  try {
    const data = await api.updateCheckStatus()
    checkResult.value = data.result
  } catch {
    // no cached result yet — leave blank rather than erroring out the page
  }
}

// Polls /api/local/system/update-progress — the single source of truth
// for "is an update running right now" (backed by a real is-active check
// on the device, not just this tab's own memory of having clicked
// something), so a page reload or a second browser tab still shows an
// update that's already in flight, including one the nightly timer
// started rather than a click here.
async function pollProgress() {
  try {
    const data = await api.updateProgress()
    updateRunning.value = data.running
    progress.value = data.progress
    if (!data.running) {
      stopPolling()
      await loadVersions()
      await loadLastCheck()
    }
  } catch {
    // transient poll failure — next tick tries again
  }
}

function startPolling() {
  if (progressTimer) return
  pollProgress()
  progressTimer = setInterval(pollProgress, 2000)
}

function stopPolling() {
  if (progressTimer) {
    clearInterval(progressTimer)
    progressTimer = null
  }
}

onMounted(async () => {
  loadVersions()
  loadLastCheck()
  const data = await api.updateProgress().catch(() => null)
  if (data) {
    updateRunning.value = data.running
    progress.value = data.progress
  }
  if (data?.running) startPolling()
})

onUnmounted(stopPolling)

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

async function updateNow() {
  applyError.value = null
  try {
    await api.triggerUpdateApply()
  } catch (err) {
    // A 409 here means something's already running (another click, or the
    // nightly timer beat us to it) — not a real failure, so still start
    // polling to show its progress instead of just leaving an error up.
    applyError.value = err.message
  }
  startPolling()
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

const confirmingUnpair = ref(false)
const unpairing = ref(false)
const unpairError = ref(null)

async function unpair() {
  unpairing.value = true
  unpairError.value = null
  try {
    await api.unpair()
    await reboot()
    // reboot() never throws (it swallows its own errors, surfacing a real
    // failure via rebootError instead — see its own comment) — so if the
    // device didn't actually reboot, unpairing.value must be cleared here,
    // not just on this function's own catch path, or "Unpairing…" is left
    // stuck forever with no indication rebootError is the real story.
  } catch (err) {
    unpairError.value = err.message
  } finally {
    unpairing.value = false
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
    <h1>System</h1>

    <section class="block">
      <h2>Software Update</h2>
      <p class="hint">
        Checks the AnnouncementSlides server for a newer OS image or local
        app release. Reports "not paired" until this device has been paired
        (see "Pair This Device" on the home screen).
      </p>

      <div class="result tile">
        <div class="row">
          <span class="label">Current OS version</span>
          <span>{{ versions.image_version || '—' }}</span>
        </div>
        <div class="row">
          <span class="label">Current app version</span>
          <span>{{ versions.app_version || '—' }}</span>
        </div>
      </div>

      <button class="tile action" :disabled="checking || updateRunning" @click="checkForUpdate">
        {{ checking ? 'Checking…' : 'Check for Update' }}
      </button>

      <p v-if="checkError" class="pill warn">{{ checkError }}</p>

      <!-- An update is running (this tab's click, another tab's click, or the
           nightly timer) — the progress block replaces the check result
           entirely while it's active, since neither is meaningful until it's
           done. -->
      <div v-if="updateRunning" class="result tile">
        <div class="row">
          <span class="label">
            {{ progress?.kind === 'os' ? `OS ${progress.release_type || ''} update`.trim() : 'Local app update' }}
            in progress
          </span>
          <span v-if="progress?.version">v{{ progress.version }}</span>
        </div>
        <p class="hint" style="margin: 0 0 0.6rem;">{{ progress?.phase || 'Working…' }}</p>
        <div class="progress-track">
          <div
            class="progress-fill"
            :class="{ indeterminate: progress?.percent == null }"
            :style="progress?.percent != null ? { width: progress.percent + '%' } : {}"
          />
        </div>
        <p v-if="progress?.percent != null" class="hint" style="margin: 0.35rem 0 0; text-align: right;">
          {{ progress.percent }}%
        </p>
      </div>

      <div v-else-if="checkResult && !updateInfo" class="result tile">
        <p class="pill warn">{{ checkResult.output || 'Update check did not return a usable result.' }}</p>
      </div>

      <div v-else-if="updateInfo" class="result tile">
        <div v-if="nextUpdate" class="row">
          <span class="label">Update available</span>
          <span class="pill warn">
            {{ nextUpdate.kind === 'os' ? `OS ${nextUpdate.releaseType}` : 'Local app' }}
            v{{ nextUpdate.version }}
          </span>
        </div>
        <p v-else class="pill ok">Up to date — no update available.</p>

        <p v-if="nextUpdate?.kind === 'os' && nextUpdate.releaseType === 'full'" class="hint">
          This is a full OS image update — the device will reboot on its own partway through.
        </p>

        <button v-if="nextUpdate" class="tile action" @click="updateNow">Update Now</button>
      </div>

      <p v-if="applyError" class="pill warn">{{ applyError }}</p>
      <p v-if="!updateRunning && progress?.done" class="pill ok">
        {{ progress.result === 'installed' || progress.result === 'success'
          ? 'Update installed.'
          : progress.result === 'tryboot_triggered'
            ? 'Update staged — rebooting to apply it.'
            : `Update result: ${progress.result || 'unknown'}` }}
      </p>
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
.status-block { margin-top: 0.5rem; }
.progress-track {
  height: 0.6rem;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  border-radius: 999px;
  background: var(--accent, #6c8cff);
  transition: width 0.4s ease;
}
.progress-fill.indeterminate {
  width: 40%;
  animation: progress-indeterminate 1.2s ease-in-out infinite;
}
@keyframes progress-indeterminate {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(250%); }
}
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
