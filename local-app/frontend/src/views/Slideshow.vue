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

// Manual slide navigation. Right/Down and MediaFastForward advance one
// slide, Left/Up and MediaRewind go back one slide. MediaTrackNext/
// MediaTrackPrevious ("skip") jump straight to the last/first slide.
// Confirmed via Settings > Key Debug on real hardware — the remote has 4
// distinct media buttons, not aliases of one another. Space and P are a
// plain-keyboard equivalent to the remote's MediaPlayPause button, for
// anyone testing/operating this without the physical remote.
const NEXT_KEYS = ['ArrowRight', 'ArrowDown', 'MediaFastForward']
const PREV_KEYS = ['ArrowLeft', 'ArrowUp', 'MediaRewind']
const LAST_KEYS = ['MediaTrackNext']
const FIRST_KEYS = ['MediaTrackPrevious']
const PLAY_PAUSE_KEYS = ['MediaPlayPause', ' ', 'p', 'P']

const playlist = ref([])
const settings = ref({})
const status = ref(null)
const currentIndex = ref(0)
const paused = ref(false)

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
  if (paused.value || playlist.value.length <= 1) return
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

// Jumping resets the advance timer so the interval waits a full
// slideIntervalMs() from the manual change, rather than possibly
// auto-advancing again a moment later.
function goToIndex(index) {
  const len = playlist.value.length
  if (len === 0) return
  currentIndex.value = ((index % len) + len) % len
  restartAdvanceTimer()
}

function togglePause() {
  paused.value = !paused.value
  restartAdvanceTimer()
}

function onKeydown(event) {
  if (NEXT_KEYS.includes(event.key)) { event.preventDefault(); goToIndex(currentIndex.value + 1) }
  else if (PREV_KEYS.includes(event.key)) { event.preventDefault(); goToIndex(currentIndex.value - 1) }
  else if (LAST_KEYS.includes(event.key)) { event.preventDefault(); goToIndex(playlist.value.length - 1) }
  else if (FIRST_KEYS.includes(event.key)) { event.preventDefault(); goToIndex(0) }
  else if (PLAY_PAUSE_KEYS.includes(event.key)) { event.preventDefault(); togglePause() }
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
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  if (advanceTimer) clearInterval(advanceTimer)
  if (refreshTimer) clearInterval(refreshTimer)
  if (statusTimer) clearInterval(statusTimer)
  window.removeEventListener('keydown', onKeydown)
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

    <div v-if="paused" class="pause-indicator" :title="t('slideshow.paused')">
      <span class="pause-icon" />
      {{ t('slideshow.paused') }}
    </div>
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
.pause-indicator {
  position: absolute;
  bottom: 1.5rem;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 1rem;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 1rem;
  letter-spacing: 0.02em;
}
.pause-icon {
  width: 0.9rem;
  height: 0.9rem;
  background:
    linear-gradient(#fff, #fff) 0 0 / 35% 100% no-repeat,
    linear-gradient(#fff, #fff) 100% 0 / 35% 100% no-repeat;
}
</style>
