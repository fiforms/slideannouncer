<script setup>
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { features, loadFeatures, setFeature } from '../../features.js'
import ToggleSwitch from '../../components/ToggleSwitch.vue'

const { t } = useI18n()

// Screen resolution — moved here from the old standalone Screens page.
const resolution = ref(null)
const resolutionSaving = ref(false)
const resolutionError = ref(null)

async function loadResolution() {
  try {
    const data = await api.screenResolutionStatus()
    resolution.value = data.screen_resolution
  } catch {
    // leave blank rather than erroring out the page
  }
}

async function selectResolution(value) {
  if (value === resolution.value || resolutionSaving.value) return
  resolutionSaving.value = true
  resolutionError.value = null
  try {
    const data = await api.setScreenResolution(value)
    resolution.value = data.screen_resolution
  } catch (err) {
    resolutionError.value = err.message
  } finally {
    resolutionSaving.value = false
  }
}

// Optional features — each switch also adds/removes that feature's own
// page under Advanced in the rail (SettingsLayout.vue reads the same
// features.js state).
const featureSaving = ref(null)
const featureError = ref(null)

async function toggleFeature(name, enabled) {
  if (featureSaving.value) return
  featureSaving.value = name
  featureError.value = null
  try {
    await setFeature(name, enabled)
  } catch (err) {
    featureError.value = err.message
  } finally {
    featureSaving.value = null
  }
}

onMounted(() => {
  loadResolution()
  loadFeatures()
})
</script>

<template>
  <div class="settings-page">
    <section class="tile panel">
      <div class="panel-title"><h2>{{ t('settings.screens.resolution') }}</h2></div>
      <div class="actions">
        <button
          class="tile action"
          :class="{ active: resolution === '4k' }"
          :disabled="resolutionSaving"
          @click="selectResolution('4k')"
        >
          {{ t('settings.screens.resolution4k') }}
        </button>
        <button
          class="tile action"
          :class="{ active: resolution === '1080p' }"
          :disabled="resolutionSaving"
          @click="selectResolution('1080p')"
        >
          {{ t('settings.screens.resolution1080p') }}
        </button>
      </div>
      <p v-if="resolutionError" class="pill warn note">{{ resolutionError }}</p>
      <p class="hint note">{{ t('settings.screens.multiMonitorHint') }}</p>
    </section>

    <section class="tile panel">
      <div class="panel-title"><h2>{{ t('settings.advanced.featuresTitle') }}</h2></div>

      <div class="feature">
        <div class="feature-text">
          <span class="feature-name">{{ t('settingsLayout.revelationPeering') }}</span>
          <span class="hint">{{ t('settings.advanced.revelationHint') }}</span>
        </div>
        <ToggleSwitch
          :model-value="features.revelation"
          :disabled="!features.loaded || featureSaving === 'revelation'"
          @update:model-value="toggleFeature('revelation', $event)"
        />
      </div>

      <div class="feature">
        <div class="feature-text">
          <span class="feature-name">{{ t('settingsLayout.videoReceiver') }}</span>
          <span class="hint">{{ t('settings.advanced.srtSinkHint') }}</span>
          <span v-if="features.srtSink && !features.srtSinkServerAllows" class="pill warn">
            {{ t('settings.srtSink.serverDisabled') }}
          </span>
        </div>
        <ToggleSwitch
          :model-value="features.srtSink"
          :disabled="!features.loaded || featureSaving === 'srtSink'"
          @update:model-value="toggleFeature('srtSink', $event)"
        />
      </div>

      <p v-if="featureError" class="pill warn note">{{ featureError }}</p>
    </section>
  </div>
</template>

<style scoped>
.hint {
  color: var(--text-dim);
  margin: 0;
  font-size: 0.9rem;
}
.note {
  margin: 0.6rem 0 0;
}
.action {
  padding: 0.75rem 1.5rem;
  font-size: 1rem;
}
.action.active {
  border-color: var(--accent);
  color: var(--accent);
}
.actions {
  display: flex;
  gap: 1rem;
  margin-top: 0.35rem;
}
.feature {
  display: flex;
  align-items: center;
  gap: 1.5rem;
  padding: 0.6rem 0;
}
.feature + .feature {
  border-top: var(--line) solid var(--border);
}
.feature-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.2rem;
}
.feature-name {
  font-weight: 600;
}
</style>
