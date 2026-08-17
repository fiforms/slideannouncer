<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../api.js'
import { setLocale } from '../i18n.js'

const { t } = useI18n()

// Unattended kiosk display — mirrors the web slideshow's crossfade/timing
// (resources/js/Components/SlideshowModal.vue: 10s default interval, 1s
// crossfade) but with no on-screen controls, since there's no one at the
// TV to click them. Settings/unpair stay reachable only via the Menu
// button (remoteNav.js's global MENU_KEYS listener), per SLIDE_ANNOUNCER.md
// "Kiosk display": "not exposed on the main slideshow."

const REFRESH_INTERVAL_MS = 60000 // matches sync.py's own poll cadence — no point checking faster
const STATUS_INTERVAL_MS = 15000
const DEFAULT_INTERVAL_SECONDS = 10

const playlist = ref([])
const settings = ref({})
const status = ref(null)
const currentIndex = ref(0)

let advanceTimer = null
let refreshTimer = null
let statusTimer = null

const currentSlide = computed(() => playlist.value[currentIndex.value] ?? null)

const needsAttention = computed(() => {
  if (!status.value) return false
  if (!status.value.paired) return true
  return !!status.value.sync?.last_error
})

function slideIntervalMs() {
  const seconds = settings.value?.interval_seconds
  return (typeof seconds === 'number' && seconds > 0 ? seconds : DEFAULT_INTERVAL_SECONDS) * 1000
}

function restartAdvanceTimer() {
  if (advanceTimer) clearInterval(advanceTimer)
  advanceTimer = null
  if (playlist.value.length <= 1) return
  advanceTimer = setInterval(() => {
    currentIndex.value = (currentIndex.value + 1) % playlist.value.length
  }, slideIntervalMs())
}

async function refreshPlaylist() {
  try {
    const data = await api.slideshow()
    playlist.value = data.playlist || []
    settings.value = data.settings || {}
    if (currentIndex.value >= playlist.value.length) currentIndex.value = 0
    restartAdvanceTimer()
  } catch {
    // Leave whatever's already on screen — mirrors sync.py leaving the
    // on-disk cache untouched on failure. A fetch hiccup here shouldn't
    // blank a display that's otherwise fine.
  }
}

async function refreshStatus() {
  try {
    status.value = await api.localStatus()
    // Keeps the on-screen language current with whatever the server (or,
    // pre-pairing, the boot-yaml hint) reports — see i18n.js's setLocale().
    setLocale(status.value.language)
  } catch {
    // Attention indicator just won't update this cycle.
  }
}

onMounted(async () => {
  await refreshPlaylist()
  await refreshStatus()
  refreshTimer = setInterval(refreshPlaylist, REFRESH_INTERVAL_MS)
  statusTimer = setInterval(refreshStatus, STATUS_INTERVAL_MS)
})

onUnmounted(() => {
  if (advanceTimer) clearInterval(advanceTimer)
  if (refreshTimer) clearInterval(refreshTimer)
  if (statusTimer) clearInterval(statusTimer)
})
</script>

<template>
  <div class="kiosk">
    <transition name="crossfade" mode="out-in">
      <img
        v-if="currentSlide"
        :key="currentSlide.id"
        :src="currentSlide.media_url"
        class="slide-image"
      >
      <div v-else class="empty-state" key="empty">
        <p v-if="status && !status.paired">{{ t('slideshow.notPaired') }}</p>
        <p v-else>{{ t('slideshow.waiting') }}</p>
      </div>
    </transition>

    <div v-if="needsAttention" class="attention-dot" :title="t('slideshow.needsAttention')" />
  </div>
</template>

<style scoped>
.kiosk {
  position: fixed;
  inset: 0;
  background: #000;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  cursor: none;
}
.slide-image {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.crossfade-enter-active,
.crossfade-leave-active {
  transition: opacity 1s ease;
}
.crossfade-enter-from,
.crossfade-leave-to {
  opacity: 0;
}
.empty-state {
  color: var(--text-dim);
  font-size: 1.5rem;
  text-align: center;
  padding: 0 2rem;
}
.attention-dot {
  position: absolute;
  top: 1rem;
  right: 1rem;
  width: 0.75rem;
  height: 0.75rem;
  border-radius: 50%;
  background: var(--danger);
  box-shadow: 0 0 0 4px rgba(255, 107, 107, 0.25);
}
</style>
