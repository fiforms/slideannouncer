<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { mountWidget } from '../widgetHost.js'
import { location as screenLocation } from '../slideshowState.js'

// One widget placement: a box at its canvas position/size that the widget
// module draws into. WidgetLayer keys it so any change remounts cleanly.
const props = defineProps({
  placement: { type: Object, required: true },
  // Keep the widget alive this long after unmount, so a parent's leave
  // transition (which only holds back the root DOM node, not child
  // components) can fade it out instead of it vanishing at once.
  lingerMs: { type: Number, default: 0 },
})
// Fired once the widget has painted (or failed / timed out); see widgetHost.js.
const emit = defineEmits(['ready'])

const { locale } = useI18n()
const el = ref(null)
const painted = ref(false)
let handle = null

onMounted(() => {
  handle = mountWidget(el.value, props.placement, locale.value, screenLocation.value)
  handle.ready.then(() => {
    painted.value = true
    emit('ready')
  })
})
onBeforeUnmount(() => {
  const h = handle
  if (props.lingerMs > 0) setTimeout(() => h?.dispose(), props.lingerMs)
  else h?.dispose()
})
</script>

<template>
  <div
    ref="el"
    class="widget-box"
    :style="{
      left: `${placement.x}px`, top: `${placement.y}px`,
      width: `${placement.w}px`, height: `${placement.h}px`,
      opacity: painted ? (placement.opacity ?? 1) : 0,
    }"
  />
</template>

<style scoped>
.widget-box {
  position: absolute;
  overflow: hidden;
  transition: opacity 0.5s ease;
}
</style>
