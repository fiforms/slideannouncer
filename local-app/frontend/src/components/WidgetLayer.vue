<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import WidgetBox from './WidgetBox.vue'

// Live overlay widgets above a slide's overlay image — the kiosk twin of
// the main app's resources/js/Components/Widgets/WidgetLayer.vue. Fills
// its (relative) parent and maps the overlay's 1920×1080 canvas into it
// with the same object-fit: contain fit as the overlay <img>, so a widget
// lands exactly where it was placed in the editor at any screen
// resolution. Widgets draw in canvas units; the stage's CSS scale does the
// rest. Unmounting (the slide changing) runs every widget's cleanup.
const CANVAS = { w: 1920, h: 1080 }

const props = defineProps({
  widgets: { type: Array, default: () => [] },
})

const root = ref(null)
const fit = ref(null)

function measure() {
  const box = root.value
  if (!box?.clientWidth || !box?.clientHeight) return
  const scale = Math.min(box.clientWidth / CANVAS.w, box.clientHeight / CANVAS.h)
  fit.value = {
    scale,
    x: (box.clientWidth - CANVAS.w * scale) / 2,
    y: (box.clientHeight - CANVAS.h * scale) / 2,
  }
}

let observer
onMounted(() => {
  measure()
  observer = new ResizeObserver(measure)
  observer.observe(root.value)
})
onBeforeUnmount(() => observer?.disconnect())

const placements = computed(() => props.widgets.filter((w) => w.entry_url))

// Remount on any change a widget can't be expected to react to itself
// (a re-synced placement with new params, size or bundle version).
function key(p) {
  return `${p.id}:${p.entry_url}:${p.w}x${p.h}:${JSON.stringify(p.params ?? {})}`
}
</script>

<template>
  <div ref="root" class="widget-layer">
    <div
      v-if="fit && placements.length"
      class="widget-stage"
      :style="{
        left: `${fit.x}px`, top: `${fit.y}px`,
        width: `${CANVAS.w}px`, height: `${CANVAS.h}px`,
        transform: `scale(${fit.scale})`,
      }"
    >
      <WidgetBox v-for="p in placements" :key="key(p)" :placement="p" />
    </div>
  </div>
</template>

<style scoped>
.widget-layer {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
}
.widget-stage {
  position: absolute;
  transform-origin: top left;
}
</style>
