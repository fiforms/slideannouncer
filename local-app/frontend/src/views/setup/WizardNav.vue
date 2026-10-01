<script setup>
import { nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'

// Back/Next row at the bottom of each setup step. Back goes to `back`
// (the same place the remote's Back key goes — the route's meta.parent);
// Next just emits, since each step decides what moving on involves.
const props = defineProps({
  back: { type: String, default: null },
  nextLabel: { type: String, default: null },
  nextDisabled: { type: Boolean, default: false },
  // Hides Next entirely, for a step whose own form is the way forward.
  hideNext: { type: Boolean, default: false },
  // Put the remote's focus on Next rather than the page's first control —
  // as soon as it's both requested and enabled (a step may only know it
  // wants this after a fetch), and only once, so it never pulls focus back
  // from wherever someone has since moved it.
  autofocusNext: { type: Boolean, default: false },
})
const emit = defineEmits(['next'])

const nextButton = ref(null)
let autofocused = false
watch(
  () => props.autofocusNext && !props.nextDisabled,
  (ready) => {
    if (!ready || autofocused) return
    autofocused = true
    nextTick(() => nextButton.value?.focus())
  },
  { immediate: true },
)

const router = useRouter()
const { t } = useI18n()
</script>

<template>
  <div class="wizard-nav">
    <button v-if="back" type="button" class="tile nav-button" @click="router.replace(back)">
      ← {{ t('setup.back') }}
    </button>
    <span class="spacer" />
    <slot />
    <button
      v-if="!hideNext"
      type="button"
      ref="nextButton"
      class="tile nav-button primary"
      :disabled="nextDisabled"
      :autofocus="autofocusNext"
      @click="emit('next')"
    >
      {{ nextLabel || t('setup.next') }} →
    </button>
  </div>
</template>

<style scoped>
.wizard-nav {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-top: 2rem;
}
.spacer { flex: 1; }
.nav-button {
  padding: 0.9rem 1.8rem;
  font-size: 1.1rem;
  white-space: nowrap;
}
.nav-button.primary {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
  font-weight: 600;
}
</style>
