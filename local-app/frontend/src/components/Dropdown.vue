<script setup>
// Remote-friendly select. A native <select> opens Chromium's own popup,
// which on this kiosk (Wayland, no window chrome) remoteNav.js can't see
// into or close with Back — so this is a button that opens an in-page
// option list instead. The open list is a data-nav-modal: remoteNav.js
// scopes Up/Down to it (wrapping, via data-nav-wrap), focuses the current
// choice (its [autofocus]), closes it on Back (the `navclose` event), and
// puts focus back on the button once it's gone.
import { computed, onUnmounted, ref, watch } from 'vue'

const props = defineProps({
  modelValue: { default: null },
  // [{ value, label }] — value may be null (e.g. a "Default" choice).
  options: { type: Array, required: true },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const open = ref(false)
const root = ref(null)

const selectedLabel = computed(() =>
  props.options.find((opt) => opt.value === props.modelValue)?.label ?? '—')

function choose(opt) {
  open.value = false
  if (opt.value !== props.modelValue) emit('update:modelValue', opt.value)
}

// Pointer users: a click anywhere outside closes it, like a real select.
function onPointerDown(event) {
  if (root.value && !root.value.contains(event.target)) open.value = false
}

watch(open, (isOpen) => {
  if (isOpen) document.addEventListener('pointerdown', onPointerDown)
  else document.removeEventListener('pointerdown', onPointerDown)
})

onUnmounted(() => document.removeEventListener('pointerdown', onPointerDown))
</script>

<template>
  <div ref="root" class="dropdown">
    <button
      type="button"
      class="tile trigger"
      :disabled="disabled"
      aria-haspopup="listbox"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span>{{ selectedLabel }}</span>
      <span class="caret" aria-hidden="true">▾</span>
    </button>
    <div
      v-if="open"
      class="menu"
      role="listbox"
      data-nav-modal
      data-nav-wrap
      @navclose="open = false"
    >
      <button
        v-for="opt in options"
        :key="String(opt.value)"
        type="button"
        role="option"
        class="option"
        :class="{ selected: opt.value === modelValue }"
        :aria-selected="opt.value === modelValue"
        :autofocus="opt.value === modelValue"
        @click="choose(opt)"
      >
        <span class="check" aria-hidden="true">{{ opt.value === modelValue ? '✓' : '' }}</span>
        {{ opt.label }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.dropdown {
  position: relative;
  display: inline-block;
  min-width: 14rem;
}
.trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  width: 100%;
  padding: 0.65rem 1rem;
  font-size: 1rem;
}
.caret { color: var(--text-dim); }
.menu {
  position: absolute;
  top: calc(100% + 0.3rem);
  left: 0;
  min-width: 100%;
  z-index: 50;
  display: flex;
  flex-direction: column;
  padding: 0.3rem;
  background: var(--panel);
  border: var(--line) solid var(--accent);
  border-radius: 0.6rem;
  box-shadow: 0 0.6rem 1.6rem rgba(0, 0, 0, 0.5);
}
.option {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.55rem 0.9rem;
  border: none;
  border-radius: 0.4rem;
  background: transparent;
  text-align: left;
  white-space: nowrap;
}
.option:hover,
.option:focus-visible {
  background: var(--panel-hover);
}
.option.selected { color: var(--accent); font-weight: 600; }
.check {
  display: inline-block;
  width: 1rem;
}
</style>
