<script setup>
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { product } from '../../product.js'
import Dropdown from '../../components/Dropdown.vue'
import { setLocale, LANGUAGE_OPTIONS } from '../../i18n.js'

const { t, locale } = useI18n()

// One language for both this screen and the slides, shared with the
// website: picking one here applies at once and is pushed to the server.
const languageSaving = ref(false)
const languageError = ref(null)

async function selectLanguage(code) {
  if (code === locale.value || languageSaving.value) return
  languageSaving.value = true
  languageError.value = null
  try {
    const data = await api.setLanguage(code)
    setLocale(data.language)
  } catch (err) {
    languageError.value = err.message
  } finally {
    languageSaving.value = false
  }
}

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

// Timezone — two pickers (area, then place) rather than one list of ~400 names,
// which the remote couldn't scroll sensibly. UTC has no place, so picking it as
// an "area" saves straight away.
const timezone = ref(null)
const timezoneSource = ref(null)
const timezoneList = ref([])
const area = ref(null)
const timezoneSaving = ref(false)
const timezoneError = ref(null)

const splitZone = (name) => (name.includes('/') ? [name.slice(0, name.indexOf('/')), name.slice(name.indexOf('/') + 1)] : [name, null])
const areaOptions = computed(() =>
  [...new Set(timezoneList.value.map((name) => splitZone(name)[0]))].map((value) => ({ value, label: value })))
const placeOptions = computed(() =>
  timezoneList.value
    .map(splitZone)
    .filter(([a, place]) => a === area.value && place)
    .map(([a, place]) => ({ value: `${a}/${place}`, label: place.replaceAll('_', ' ') })))

async function loadTimezone() {
  try {
    const data = await api.timezoneStatus()
    timezone.value = data.timezone
    timezoneSource.value = data.source
    timezoneList.value = data.timezones ?? []
    area.value = data.timezone ? splitZone(data.timezone)[0] : null
  } catch {
    // leave blank rather than erroring out the page
  }
}

async function saveTimezone(value) {
  if (value === timezone.value || timezoneSaving.value) return
  timezoneSaving.value = true
  timezoneError.value = null
  try {
    const data = await api.setTimezone(value)
    timezone.value = data.timezone
    timezoneSource.value = data.source
  } catch (err) {
    timezoneError.value = err.message
  } finally {
    timezoneSaving.value = false
  }
}

function selectArea(value) {
  area.value = value
  if (!splitZone(value)[1] && timezoneList.value.includes(value)) saveTimezone(value)
}

onMounted(() => {
  loadResolution()
  loadTimezone()
  product.loadSettings()
})
</script>

<template>
  <div class="settings-page">
    <section class="tile panel">
      <div class="panel-title"><h2>{{ t('settings.advanced.languageTitle') }}</h2></div>
      <Dropdown
        :model-value="locale"
        :options="LANGUAGE_OPTIONS"
        :disabled="languageSaving"
        @update:model-value="selectLanguage"
      />
      <p v-if="languageError" class="pill warn note">{{ languageError }}</p>
      <p class="hint note">{{ t('settings.advanced.languageHint') }}</p>
    </section>

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

    <section v-if="timezoneList.length" class="tile panel">
      <div class="panel-title"><h2>{{ t('settings.advanced.timezoneTitle') }}</h2></div>
      <p class="current">{{ t('settings.advanced.timezoneCurrent', { zone: timezone ?? '—' }) }}</p>
      <div class="actions">
        <Dropdown :model-value="area" :options="areaOptions" :disabled="timezoneSaving" @update:model-value="selectArea" />
        <Dropdown
          v-if="placeOptions.length"
          :model-value="timezone"
          :options="placeOptions"
          :disabled="timezoneSaving"
          @update:model-value="saveTimezone"
        />
      </div>
      <p v-if="timezoneError" class="pill warn note">{{ timezoneError }}</p>
      <p class="hint note">{{ t('settings.advanced.timezoneHint') }}</p>
    </section>

    <component :is="product.advancedSection" v-if="product.advancedSection" />
  </div>
</template>

<style scoped>
.hint {
  color: var(--text-dim);
  margin: 0;
  font-size: 0.9rem;
}
.current {
  margin: 0 0 0.5rem;
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
</style>
