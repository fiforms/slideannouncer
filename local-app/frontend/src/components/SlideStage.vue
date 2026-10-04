<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import WidgetLayer from './WidgetLayer.vue'

// One slide's full visual stack: media, overlay image and widget layer —
// the kiosk twin of the main app's resources/js/Components/SlideStage.vue.
// views/Slideshow.vue mounts a stage *before* it is shown (invisible, but
// laid out and loading) and only fades it in once it emits `ready`: the
// image/video, the overlay and every widget have all loaded and painted. So
// a slide change never reveals half-loaded graphics on the TV.
const props = defineProps({
  slide: { type: Object, required: true },
  // Faded in. A stage starts hidden and is made visible by its parent.
  visible: { type: Boolean, default: false },
  // The slide on screen: its video plays; a stage that stops being
  // active (the next one took over) pauses.
  active: { type: Boolean, default: false },
})

const emit = defineEmits(['ready', 'ended'])

// A broken or very slow asset must not stall the show forever: after this
// the stage reports ready anyway and shows whatever it has.
const READY_TIMEOUT_MS = 20000

const videoEl = ref(null)

const waiting = new Set(['media'])
if (props.slide.overlay_media_url) waiting.add('overlay')
if (props.slide.widgets?.length) waiting.add('widgets')

let done = false
let gone = false

// Two animation frames after the last asset lands, so it has really been
// painted (not merely decoded) before the parent starts the fade. The
// timeout covers a throttled/hidden page where frames never fire.
const nextPaint = () => new Promise((resolve) => {
  const t = setTimeout(resolve, 200)
  requestAnimationFrame(() => requestAnimationFrame(() => { clearTimeout(t); resolve() }))
})

async function finish() {
  if (done) return
  done = true
  await nextPaint()
  if (!gone) emit('ready')
}

function settle(name) {
  if (waiting.delete(name) && !waiting.size) finish()
}

const timeout = setTimeout(() => {
  if (done) return
  console.warn(`Slide ${props.slide.id} not fully loaded after ${READY_TIMEOUT_MS}ms; showing it anyway`, [...waiting])
  finish()
}, READY_TIMEOUT_MS)

onBeforeUnmount(() => {
  gone = true
  clearTimeout(timeout)
})

// `load` fires when the bytes are in; decode() additionally waits for the
// bitmap to be decoded off the main thread, which matters on a Pi: a big
// image otherwise paints in strips as it decodes.
async function imageLoaded(name, event) {
  try { await event.target.decode() } catch { /* broken image: settle below */ }
  settle(name)
}

// Plays with sound — kiosk-start.sh launches Chromium with
// --autoplay-policy=no-user-gesture-required specifically so this succeeds
// with no prior interaction (there's never anyone at the TV to click
// anything). The muted retry is just a safety net in case that flag is
// ever missing or this is run in a non-Chromium browser for testing.
async function playWithSound() {
  const el = videoEl.value
  if (!el) return
  el.muted = false
  try {
    await el.play()
  } catch {
    el.muted = true
    try { await el.play() } catch { /* give up silently */ }
  }
}

watch(() => props.active, (active) => {
  if (active) playWithSound()
  else videoEl.value?.pause()
})

defineExpose({ videoEl })
</script>

<template>
  <div class="slide-layers" :class="{ visible }">
    <video
      v-if="slide.mime_type?.startsWith('video/')"
      ref="videoEl"
      :src="slide.media_url"
      :loop="slide.video_playback_mode === 'loop'"
      preload="auto"
      playsinline
      class="slide-image"
      @canplay="settle('media')"
      @error="settle('media')"
      @ended="emit('ended')"
    />
    <img
      v-else
      :src="slide.media_url"
      class="slide-image"
      @load="imageLoaded('media', $event)"
      @error="settle('media')"
    >
    <img
      v-if="slide.overlay_media_url"
      :src="slide.overlay_media_url"
      class="slide-image overlay"
      @load="imageLoaded('overlay', $event)"
      @error="settle('overlay')"
    >
    <WidgetLayer v-if="slide.widgets?.length" :widgets="slide.widgets" @ready="settle('widgets')" />
  </div>
</template>

<style scoped>
.slide-layers {
  position: absolute;
  inset: 0;
  opacity: 0;
  transition: opacity 1s ease;
}
.slide-layers.visible {
  opacity: 1;
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
</style>
