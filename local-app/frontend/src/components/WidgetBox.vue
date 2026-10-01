<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { mountWidget } from '../widgetHost.js'

// One widget placement: a box at its canvas position/size that the widget
// module draws into. WidgetLayer keys it so any change remounts cleanly.
const props = defineProps({
  placement: { type: Object, required: true },
})

const { locale } = useI18n()
const el = ref(null)
let handle = null

onMounted(() => {
  handle = mountWidget(el.value, props.placement, locale.value)
})
onBeforeUnmount(() => handle?.dispose())
</script>

<template>
  <div
    ref="el"
    class="widget-box"
    :style="{
      left: `${placement.x}px`, top: `${placement.y}px`,
      width: `${placement.w}px`, height: `${placement.h}px`,
      opacity: placement.opacity ?? 1,
    }"
  />
</template>

<style scoped>
.widget-box {
  position: absolute;
  overflow: hidden;
}
</style>
