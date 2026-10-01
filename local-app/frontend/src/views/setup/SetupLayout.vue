<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'

// Shell for the first-run setup wizard (router.js's /setup routes): a
// progress bar across the top and the current step below. Each step
// renders its own Back/Next row (WizardNav.vue), since what Next does —
// save a language, save a name — is the step's business. The WiFi list/
// connect screens are the very same components Settings > Network uses,
// mounted here with meta.networkBase pointing back into the wizard.
const STEPS = ['welcome', 'network', 'name', 'pairing', 'done']

const route = useRoute()
const { t } = useI18n()
const currentIndex = computed(() => STEPS.indexOf(route.meta.step))
</script>

<template>
  <div class="setup">
    <!-- progress-* class names, not step-*: a parent's scoped styles also
         reach each child view's root element, so anything generic here
         would leak into the step views themselves. -->
    <ol class="progress">
      <li
        v-for="(step, i) in STEPS"
        :key="step"
        class="progress-item"
        :class="{ 'progress-item--done': i < currentIndex, 'progress-item--current': i === currentIndex }"
      >
        <span class="progress-number">{{ i < currentIndex ? '✓' : i + 1 }}</span>
        <span>{{ t(`setup.steps.${step}`) }}</span>
      </li>
    </ol>
    <main class="body">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.setup {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
  height: 100%;
  max-width: 64rem;
  margin: 0 auto;
}
.progress {
  display: flex;
  justify-content: center;
  gap: 2.5rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.progress-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  color: var(--text-dim);
}
.progress-number {
  flex-shrink: 0;
  display: grid;
  place-items: center;
  width: 2rem;
  height: 2rem;
  border-radius: 50%;
  border: var(--line-thick) solid var(--border);
  font-weight: 700;
  font-size: 0.95rem;
}
.progress-item--done .progress-number {
  border-color: var(--ok);
  color: var(--ok);
}
.progress-item--current {
  color: var(--text);
  font-weight: 600;
}
.progress-item--current .progress-number {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
}
/* Flex column so the centered steps (.setup-card) can sit in the middle
   of the screen with margin: auto; the Network/Pairing steps just fill
   the width from the top. */
.body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0.5rem;
  display: flex;
  flex-direction: column;
}
</style>
