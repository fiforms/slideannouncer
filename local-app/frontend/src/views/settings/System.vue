<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { setLocale, LANGUAGE_OPTIONS } from '../../i18n.js'
import Dropdown from '../../components/Dropdown.vue'

const { t } = useI18n()

// pairing.read_language_source() — "device" is the setup wizard's choice.
const LANGUAGE_SOURCE_KEYS = {
  server: 'settings.system.languageSourceServer',
  device: 'settings.system.languageSourceDevice',
  boot_yaml: 'settings.system.languageSourceBootYaml',
}

const checking = ref(false)
const checkError = ref(null)
const checkResult = ref(null)
const versions = ref({ image_version: null, app_version: null })
const deviceStatus = ref(null)

const applyError = ref(null)
const updateRunning = ref(false)
const progress = ref(null)
let progressTimer = null

// Same device-level choice as the setup wizard's Welcome step (pairing.py's
// LOCAL_LANGUAGE_FILE). Only editable while nothing outranks it: once an
// entity admin assigns a language from the website, that always wins (see
// read_effective_language()), so the row goes back to read-only.
const languageSaving = ref(false)
const languageError = ref(null)
const languageEditable = computed(() => deviceStatus.value && deviceStatus.value.language_source !== 'server')

async function selectLanguage(code) {
  languageSaving.value = true
  languageError.value = null
  try {
    const data = await api.setLanguage(code)
    deviceStatus.value = { ...deviceStatus.value, ...data }
    setLocale(data.language)
  } catch (err) {
    languageError.value = err.message
  } finally {
    languageSaving.value = false
  }
}

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
  <!-- No page title — the rail's highlighted "System" item already says
       where you are, and this page needs every row of vertical space to
       fit a 1080p/4K TV without scrolling. -->
  <div class="settings-page">
    <section class="tile panel">
      <div class="panel-title"><h2>{{ t('settings.system.deviceInfo') }}</h2></div>
      <!-- Not-paired shows as the Paired Entity value itself (with the
           pointer to the Pairing page) rather than a separate Paired
           yes/no row plus a hint paragraph. -->
      <div v-if="deviceStatus" class="info-grid">
        <span class="label">{{ t('settings.system.deviceLabel') }}</span>
        <span class="value">{{ (deviceStatus.paired && deviceStatus.device_name) || '—' }}</span>
        <span class="label">{{ t('settings.system.pairedEntity') }}</span>
        <span v-if="deviceStatus.paired" class="value">{{ deviceStatus.entity_name ?? '—' }}</span>
        <span v-else class="value">{{ t('settings.system.notPairedHint') }}</span>
        <span class="label">{{ t('settings.network.hostname') }}</span>
        <span class="value">{{ deviceStatus.hostname || '—' }}</span>
        <span class="label">{{ t('settings.system.language') }}</span>
        <span v-if="languageEditable" class="value">
          <Dropdown
            :model-value="deviceStatus.language || 'en'"
            :options="LANGUAGE_OPTIONS"
            :disabled="languageSaving"
            @update:model-value="selectLanguage"
          />
        </span>
        <span v-else class="value">
          {{ deviceStatus.language || '—' }}
          <span v-if="deviceStatus.language_source" class="hint">
            ({{ t(LANGUAGE_SOURCE_KEYS[deviceStatus.language_source] || 'settings.system.languageSourceBootYaml') }})
          </span>
        </span>
      </div>
      <p v-if="languageError" class="pill warn note">{{ languageError }}</p>
    </section>

    <section class="tile panel">
      <!-- The short "up to date / update available" status sits on the
           heading row itself rather than a row of its own below it. -->
      <div class="panel-title">
        <h2>{{ t('settings.system.softwareUpdate') }}</h2>
        <template v-if="!updateRunning && updateInfo">
          <span v-if="nextUpdate" class="head-status">
            <span class="label">{{ t('settings.system.updateAvailable') }}</span>
            <span class="pill warn">
              {{ nextUpdateTag }}
              {{ t('settings.system.version', { version: nextUpdate.version }) }}
            </span>
          </span>
          <span v-else class="pill ok">{{ t('settings.system.upToDate') }}</span>
        </template>
      </div>

      <div class="versions">
        <span class="version-item">
          <span class="label">{{ t('settings.system.currentOsVersion') }}</span>
          <span class="value">{{ versions.image_version || '—' }}</span>
        </span>
        <span class="version-item">
          <span class="label">{{ t('settings.system.currentAppVersion') }}</span>
          <span class="value">{{ versions.app_version || '—' }}</span>
        </span>
      </div>

      <!-- An update is running (this tab's click, another tab's click, or the
           nightly timer) — the progress block replaces the check result
           entirely while it's active, since neither is meaningful until it's
           done. -->
      <div v-if="updateRunning" class="progress-block">
        <div class="row">
          <span class="label">{{ t('settings.system.inProgress', { label: progressLabel }) }}</span>
          <span v-if="progress?.version">{{ t('settings.system.version', { version: progress.version }) }}</span>
        </div>
        <p class="hint" style="margin: 0 0 0.4rem;">{{ progress?.phase || t('settings.system.working') }}</p>
        <div class="progress-track">
          <div
            class="progress-fill"
            :class="{ indeterminate: progress?.percent == null }"
            :style="progress?.percent != null ? { width: progress.percent + '%' } : {}"
          />
        </div>
        <p v-if="progress?.percent != null" class="hint" style="margin: 0.25rem 0 0; text-align: right;">
          {{ progress.percent }}%
        </p>
      </div>

      <p v-else-if="checkResult && !updateInfo" class="pill warn note">
        {{ checkResult.output || t('settings.system.noUsableResult') }}
      </p>

      <p v-else-if="nextUpdate?.kind === 'os' && nextUpdate.releaseType === 'full'" class="hint note">
        {{ t('settings.system.fullOsRebootHint') }}
      </p>

      <div class="actions">
        <button class="tile action" :disabled="checking || updateRunning" @click="checkForUpdate">
          {{ checking ? t('settings.system.checking') : t('settings.system.checkForUpdate') }}
        </button>
        <button class="tile action" :disabled="!nextUpdate || updateRunning" @click="updateNow">
          {{ t('settings.system.updateNow') }}
        </button>
      </div>

      <p v-if="checkError" class="pill warn note">{{ checkError }}</p>
      <p v-if="applyError" class="pill warn note">{{ applyError }}</p>
      <p v-if="!updateRunning && progress?.done" class="pill ok note">
        {{ progress.result === 'installed' || progress.result === 'success'
          ? t('settings.system.updateInstalled')
          : progress.result === 'tryboot_triggered'
            ? t('settings.system.updateStaged')
            : t('settings.system.updateResult', { result: progress.result || 'unknown' }) }}
      </p>
    </section>

    <section class="tile panel">
      <div class="panel-title"><h2>{{ t('settings.system.audioOutput') }}</h2></div>
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
      <p v-if="audioOutputError" class="pill warn note">{{ audioOutputError }}</p>
    </section>

    <div>
      <router-link to="/settings/device-tools" class="tile action device-tools-link">
        {{ t('settings.system.deviceToolsLink') }}
      </router-link>
    </div>
  </div>
</template>

<style scoped>
.head-status {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.hint {
  color: var(--text-dim);
  margin-top: 0;
}
.note {
  margin: 0.6rem 0 0;
}
/* Label column (bold, dim) + value column (normal, full-white text) —
   the same pairing .versions uses inline in the Software Update box. */
.info-grid {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: center;
  gap: 0.35rem 1.5rem;
}
.label {
  color: var(--text-dim);
  font-weight: 600;
}
/* On the light heading bar (style.css .panel-title) — this scoped rule
   would otherwise outrank the bar's own darker label color. */
.panel-title .label { color: var(--title-bar-dim); }
.value {
  color: var(--text);
  overflow-wrap: anywhere;
}
.versions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem 2.5rem;
  margin-bottom: 0.75rem;
}
.version-item {
  display: flex;
  gap: 0.75rem;
}
.action {
  padding: 0.75rem 1.5rem;
  font-size: 1rem;
}
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
  margin-top: 0.35rem;
}
.progress-block { margin-bottom: 0.6rem; }
.row {
  display: flex;
  justify-content: space-between;
  margin-bottom: 0.4rem;
}
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
.device-tools-link {
  display: inline-block;
  text-decoration: none;
}
</style>
