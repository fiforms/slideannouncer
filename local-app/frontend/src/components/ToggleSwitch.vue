<script setup>
// On/off slider switch. A plain <button role="switch"> so remoteNav.js can
// focus it like any other control and Enter/click flips it.
defineProps({
  modelValue: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})
defineEmits(['update:modelValue'])
</script>

<template>
  <button
    type="button"
    role="switch"
    class="switch"
    :class="{ on: modelValue }"
    :aria-checked="modelValue"
    :disabled="disabled"
    @click="$emit('update:modelValue', !modelValue)"
  >
    <span class="knob" />
  </button>
</template>

<style scoped>
.switch {
  flex: none;
  position: relative;
  width: 3.4rem;
  height: 1.9rem;
  padding: 0;
  border-radius: 999px;
  border: var(--line) solid var(--border);
  background: var(--bg);
  transition: background-color 0.15s, border-color 0.15s;
}
.switch.on {
  background: var(--accent);
  border-color: var(--accent);
}
.knob {
  position: absolute;
  top: 50%;
  left: 0.2rem;
  width: 1.4rem;
  height: 1.4rem;
  border-radius: 50%;
  background: var(--text-dim);
  transform: translateY(-50%);
  transition: left 0.15s, background-color 0.15s;
}
.switch.on .knob {
  left: calc(100% - 1.6rem);
  background: #fff;
}
</style>
