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
const VOLUME_INDICATOR_HOLD_MS = 1500
// Volume/mute is applied by a separate process (volume-key-monitor.py)
// that this page has no other way to hear about — but Chromium receives
// the same raw AudioVolumeUp/Down/Mute keydowns volume-key-monitor.py
// reads from evdev (confirmed via Settings > Key Debug), since evdev
// nodes aren't exclusively grabbed. So rather than polling on a fixed
// interval, this page just listens for the same keys and polls once,
// after a short settle delay — long enough for volume-key-monitor.py to
// debounce, call wpctl, and persist the new value first (see that
// script's own DEBOUNCE_SECONDS). A key held down (autorepeat) re-arms
// the delay each time rather than polling per repeat, so a press-and-hold
// still ends in exactly one poll, right after release.
const VOLUME_POLL_SETTLE_MS = 250
const VOLUME_KEYS = ['AudioVolumeUp', 'AudioVolumeDown', 'AudioVolumeMute']
// Safety margin added on top of a play_through video's own reported
// duration for the fallback advance below — covers normal decode/paint
// latency, not a real wait.
const PLAY_THROUGH_FALLBACK_BUFFER_MS = 3000

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
const volume = ref(100)
const muted = ref(false)
const showVolumeIndicator = ref(false)

let advanceTimer = null
let refreshTimer = null
let statusTimer = null
let volumeSettleTimer = null
let volumeHideTimer = null
let playThroughFallbackTimer = null
// False until the first poll lands, so that poll can set the baseline
// silently instead of popping the indicator up on page load.
let hasVolumeBaseline = false

const currentSlide = computed(() => playlist.value[currentIndex.value] ?? null)

function isVideoSlide(slide) {
  return !!slide?.mime_type?.startsWith('video/')
}

const needsAttention = computed(() => {
  if (!status.value) return false
  if (!status.value.paired) return true
  return !!status.value.sync?.last_error
})

function slideIntervalMs() {
  const seconds = settings.value?.interval_seconds
  return (typeof seconds === 'number' && seconds > 0 ? seconds : DEFAULT_INTERVAL_SECONDS) * 1000
}

function clearPlayThroughFallback() {
  if (playThroughFallbackTimer) {
    clearTimeout(playThroughFallbackTimer)
    playThroughFallbackTimer = null
  }
}

function restartAdvanceTimer() {
  if (advanceTimer) clearInterval(advanceTimer)
  advanceTimer = null
  if (paused.value || playlist.value.length <= 1) return
  // A 'play_through' video advances from its 'ended' event (onVideoEnded)
  // instead of a fixed delay — skip the interval entirely for it.
  const slide = currentSlide.value
  if (isVideoSlide(slide) && slide.video_playback_mode === 'play_through') return
  // Routed through goToIndex (which calls back into this function) rather
  // than mutating currentIndex directly, so a tick that lands on a
  // play_through video is recognized immediately — otherwise this same
  // interval, started for a run of non-video/non-play_through slides,
  // would keep ticking on its old schedule straight through a
  // play_through video it was never restarted for, and cut it off
  // mid-playback instead of waiting for onVideoEnded.
  advanceTimer = setInterval(() => {
    goToIndex(currentIndex.value + 1)
  }, slideIntervalMs())
}

function onVideoEnded() {
  clearPlayThroughFallback()
  const slide = currentSlide.value
  if (isVideoSlide(slide) && slide.video_playback_mode === 'play_through') {
    goToIndex(currentIndex.value + 1)
  }
  // hold_last_frame: no-op — the <video> (not looping) naturally freezes on
  // its last frame until the interval-based advance (if any) fires.
}

// Plays with sound — kiosk-start.sh launches Chromium with
// --autoplay-policy=no-user-gesture-required specifically so this succeeds
// with no prior interaction (there's never anyone at the TV to click
// anything). The muted retry is just a safety net in case that flag is
// ever missing or this is run in a non-Chromium browser for testing.
async function playWithSound(event) {
  const el = event.target
  el.muted = false
  try {
    await el.play()
  } catch {
    el.muted = true
    try { await el.play() } catch { /* give up silently */ }
  }
}

// A play_through video is supposed to advance from onVideoEnded's 'ended'
// listener — but on real hardware, a video has been observed to visibly
// freeze on its last frame without 'ended' ever firing (most likely a
// codec/decode quirk or imprecise container duration metadata specific to
// that file/device combination, not reproducible from the code alone).
// On the web slideshow that's recoverable (a person just clicks Next);
// on an unattended kiosk it's stuck until someone walks up with the
// remote. So this schedules a fallback advance at the video's own
// reported duration (plus a small buffer for normal decode/paint
// latency) — a no-op if 'ended' fires first (onVideoEnded clears it), and
// a no-op if the slide has already moved on for some other reason
// (manual nav, playlist refresh) by the time it fires.
function onVideoLoadedMetadata(event) {
  playWithSound(event)
  clearPlayThroughFallback()
  const slide = currentSlide.value
  if (!isVideoSlide(slide) || slide.video_playback_mode !== 'play_through') return
  const el = event.target
  const expectedIndex = currentIndex.value
  const durationMs = Number.isFinite(el.duration) ? el.duration * 1000 : slideIntervalMs()
  playThroughFallbackTimer = setTimeout(() => {
    if (currentIndex.value === expectedIndex) goToIndex(expectedIndex + 1)
  }, durationMs + PLAY_THROUGH_FALLBACK_BUFFER_MS)
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
  else if (VOLUME_KEYS.includes(event.key)) { event.preventDefault(); scheduleVolumePoll() }
}

// Re-arms on every matching keydown rather than polling immediately, so a
// held/autorepeating key collapses into a single poll shortly after the
// last event instead of one per repeat.
function scheduleVolumePoll() {
  if (volumeSettleTimer) clearTimeout(volumeSettleTimer)
  volumeSettleTimer = setTimeout(refreshVolume, VOLUME_POLL_SETTLE_MS)
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

async function refreshVolume() {
  try {
    const data = await api.audioVolumeStatus()
    const changed = hasVolumeBaseline && (data.volume !== volume.value || data.muted !== muted.value)
    volume.value = data.volume
    muted.value = data.muted
    hasVolumeBaseline = true
    if (changed) {
      showVolumeIndicator.value = true
      if (volumeHideTimer) clearTimeout(volumeHideTimer)
      volumeHideTimer = setTimeout(() => { showVolumeIndicator.value = false }, VOLUME_INDICATOR_HOLD_MS)
    }
  } catch {
    // Leave whatever was last shown — a fetch hiccup shouldn't flicker
    // the indicator or reset the displayed level.
  }
}

onMounted(async () => {
  await refreshPlaylist()
  await refreshStatus()
  await refreshVolume()
  refreshTimer = setInterval(refreshPlaylist, REFRESH_INTERVAL_MS)
  statusTimer = setInterval(refreshStatus, STATUS_INTERVAL_MS)
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  if (advanceTimer) clearInterval(advanceTimer)
  if (refreshTimer) clearInterval(refreshTimer)
  if (statusTimer) clearInterval(statusTimer)
  if (volumeSettleTimer) clearTimeout(volumeSettleTimer)
  if (volumeHideTimer) clearTimeout(volumeHideTimer)
  clearPlayThroughFallback()
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="kiosk">
    <transition name="crossfade" mode="out-in">
      <div v-if="currentSlide" :key="currentSlide.id" class="slide-layers">
        <video
          v-if="isVideoSlide(currentSlide)"
          :src="currentSlide.media_url"
          :loop="currentSlide.video_playback_mode === 'loop'"
          playsinline
          class="slide-image"
          @ended="onVideoEnded"
          @loadedmetadata="onVideoLoadedMetadata"
        />
        <img v-else :src="currentSlide.media_url" class="slide-image">
        <img v-if="currentSlide.overlay_media_url" :src="currentSlide.overlay_media_url" class="slide-image overlay">
      </div>
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

    <div v-if="showVolumeIndicator" class="volume-indicator">
      <span class="volume-icon" :class="{ muted }" />
      <div class="volume-track">
        <div class="volume-fill" :style="{ width: (muted ? 0 : volume) + '%' }" />
      </div>
      <span class="volume-value">{{ muted ? t('slideshow.muted') : `${volume}%` }}</span>
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
.slide-layers {
  position: relative;
  width: 100%;
  height: 100%;
}
.slide-image {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.slide-image.overlay {
  position: absolute;
  inset: 0;
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
.volume-indicator {
  position: absolute;
  bottom: 1.5rem;
  right: 1.5rem;
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.5rem 1rem;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 1rem;
  letter-spacing: 0.02em;
}
.volume-icon {
  flex: none;
  width: 1rem;
  height: 1rem;
  background: #fff;
  clip-path: polygon(0% 35%, 35% 35%, 65% 5%, 65% 95%, 35% 65%, 0% 65%);
}
.volume-icon.muted {
  background: var(--danger);
}
.volume-track {
  width: 6rem;
  height: 0.35rem;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.25);
  overflow: hidden;
}
.volume-fill {
  height: 100%;
  background: #fff;
  transition: width 0.15s ease;
}
.volume-value {
  min-width: 3ch;
  text-align: right;
}
</style>
