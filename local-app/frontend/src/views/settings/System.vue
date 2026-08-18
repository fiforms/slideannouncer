<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'

const { t } = useI18n()

const checking = ref(false)
const checkError = ref(null)
const checkResult = ref(null)
const versions = ref({ image_version: null, app_version: null })
const deviceStatus = ref(null)

const applyError = ref(null)
const updateRunning = ref(false)
const progress = ref(null)
let progressTimer = null

const audioOutput = ref(null)
const audioOutputSaving = ref(false)
const audioOutputError = ref(null)

async function loadAudioOutput() {
  try {
    const data = await api.audioOutputStatus()
    audioOutput.value = data.audio_output
  } catch {
    // leave blank rather than erroring out the page
  }
}

async function selectAudioOutput(value) {
  if (value === audioOutput.value || audioOutputSaving.value) return
  audioOutputSaving.value = true
  audioOutputError.value = null
  try {
    const data = await api.setAudioOutput(value)
    audioOutput.value = data.audio_output
  } catch (err) {
    audioOutputError.value = err.message
  } finally {
    audioOutputSaving.value = false
  }
}

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

const progressLabel = computed(() => {
  if (progress.value?.kind === 'os') {
    return t('settings.system.osUpdateLabel', { type: progress.value.release_type || '' }).trim()
  }
  return t('settings.system.appUpdateLabel')
})

const nextUpdateTag = computed(() => {
  if (!nextUpdate.value) return ''
  return nextUpdate.value.kind === 'os'
    ? t('settings.system.osUpdateTag', { type: nextUpdate.value.releaseType })
    : t('settings.system.appUpdateTag')
})

async function loadVersions() {
  try {
    const status = await api.localStatus()
    versions.value = { image_version: status.image_version, app_version: status.app_version }
    deviceStatus.value = status
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
  loadAudioOutput()
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

</script>

<template>
  <div>
    <h1>{{ t('settings.system.title') }}</h1>

    <section class="block">
      <h2>{{ t('settings.system.deviceInfo') }}</h2>
      <dl v-if="deviceStatus" class="info-grid">
        <dt>{{ t('settings.system.deviceLabel') }}</dt>
        <dd v-if="deviceStatus.paired">{{ deviceStatus.device_name ?? '—' }}</dd>
        <dd v-else class="hint">{{ t('settings.system.notPairedYet') }}</dd>
        <dt>{{ t('settings.system.pairedEntity') }}</dt><dd>{{ deviceStatus.entity_name ?? '—' }}</dd>
        <dt>{{ t('settings.system.deviceUuid') }}</dt><dd>{{ deviceStatus.device_uuid ?? '—' }}</dd>
        <dt>{{ t('settings.system.currentOsVersion') }}</dt><dd>{{ versions.image_version || '—' }}</dd>
        <dt>{{ t('settings.system.currentAppVersion') }}</dt><dd>{{ versions.app_version || '—' }}</dd>
        <dt>{{ t('settings.system.language') }}</dt>
        <dd>
          {{ deviceStatus.language || '—' }}
          <span v-if="deviceStatus.language_source" class="hint">
            ({{ deviceStatus.language_source === 'server' ? t('settings.system.languageSourceServer') : t('settings.system.languageSourceBootYaml') }})
          </span>
        </dd>
        <dt>{{ t('settings.system.paired') }}</dt><dd>{{ deviceStatus.paired ? t('common.yes') : t('common.no') }}</dd>
      </dl>
      <p v-if="deviceStatus && !deviceStatus.paired" class="hint">
        {{ t('settings.system.notPairedHint') }}
      </p>
    </section>

    <section class="block">
      <div class="tile result">
        <h2>{{ t('settings.system.softwareUpdate') }}</h2>

        <!-- An update is running (this tab's click, another tab's click, or the
             nightly timer) — the progress block replaces the check result
             entirely while it's active, since neither is meaningful until it's
             done. -->
        <div v-if="updateRunning">
          <div class="row">
            <span class="label">{{ t('settings.system.inProgress', { label: progressLabel }) }}</span>
            <span v-if="progress?.version">{{ t('settings.system.version', { version: progress.version }) }}</span>
          </div>
          <p class="hint" style="margin: 0 0 0.6rem;">{{ progress?.phase || t('settings.system.working') }}</p>
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

        <p v-else-if="checkResult && !updateInfo" class="pill warn">
          {{ checkResult.output || t('settings.system.noUsableResult') }}
        </p>

        <div v-else-if="updateInfo">
          <div v-if="nextUpdate" class="row">
            <span class="label">{{ t('settings.system.updateAvailable') }}</span>
            <span class="pill warn">
              {{ nextUpdateTag }}
              {{ t('settings.system.version', { version: nextUpdate.version }) }}
            </span>
          </div>
          <p v-else class="pill ok">{{ t('settings.system.upToDate') }}</p>

          <p v-if="nextUpdate?.kind === 'os' && nextUpdate.releaseType === 'full'" class="hint">
            {{ t('settings.system.fullOsRebootHint') }}
          </p>
        </div>

        <div class="actions">
          <button class="tile action" :disabled="checking || updateRunning" @click="checkForUpdate">
            {{ checking ? t('settings.system.checking') : t('settings.system.checkForUpdate') }}
          </button>
          <button class="tile action" :disabled="!nextUpdate || updateRunning" @click="updateNow">
            {{ t('settings.system.updateNow') }}
          </button>
        </div>

        <p v-if="checkError" class="pill warn">{{ checkError }}</p>
        <p v-if="applyError" class="pill warn">{{ applyError }}</p>
        <p v-if="!updateRunning && progress?.done" class="pill ok">
          {{ progress.result === 'installed' || progress.result === 'success'
            ? t('settings.system.updateInstalled')
            : progress.result === 'tryboot_triggered'
              ? t('settings.system.updateStaged')
              : t('settings.system.updateResult', { result: progress.result || 'unknown' }) }}
        </p>
      </div>
    </section>

    <section class="block">
      <div class="tile result">
        <h2>{{ t('settings.system.audioOutput') }}</h2>
        <div class="actions">
          <button
            class="tile action"
            :class="{ active: audioOutput === 'hdmi' }"
            :disabled="audioOutputSaving"
            @click="selectAudioOutput('hdmi')"
          >
            {{ t('settings.system.audioOutputHdmi') }}
          </button>
          <button
            class="tile action"
            :class="{ active: audioOutput === 'headphones' }"
            :disabled="audioOutputSaving"
            @click="selectAudioOutput('headphones')"
          >
            {{ t('settings.system.audioOutputHeadphones') }}
          </button>
        </div>
        <p v-if="audioOutputError" class="pill warn">{{ audioOutputError }}</p>
      </div>
    </section>

    <section class="block">
      <router-link to="/settings/device-tools" class="tile action device-tools-link">
        {{ t('settings.system.deviceToolsLink') }}
      </router-link>
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
.action.active {
  border-color: var(--accent, #6c8cff);
  color: var(--accent, #6c8cff);
}
.action.danger {
  border-color: var(--danger);
  color: var(--danger);
}
.actions {
  display: flex;
  gap: 1rem;
}
.result {
  padding: 1.25rem 1.5rem;
}
.result h2 { margin-top: 0; }
.row {
  display: flex;
  justify-content: space-between;
  margin-bottom: 0.5rem;
}
.label { color: var(--text-dim); }
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
.info-grid {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.4rem 1.5rem;
  margin: 0 0 1rem;
}
.info-grid dt { color: var(--text-dim); }
.device-tools-link {
  display: inline-block;
  text-decoration: none;
}
</style>
