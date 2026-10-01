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
    <ol class="steps">
      <li
        v-for="(step, i) in STEPS"
        :key="step"
        class="step"
        :class="{ 'step--done': i < currentIndex, 'step--current': i === currentIndex }"
      >
        <span class="step-number">{{ i < currentIndex ? '✓' : i + 1 }}</span>
        <span class="step-label">{{ t(`setup.steps.${step}`) }}</span>
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
.steps {
  display: flex;
  justify-content: center;
  gap: 2.5rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.step {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  color: var(--text-dim);
}
.step-number {
  display: grid;
  place-items: center;
  width: 2rem;
  height: 2rem;
  border-radius: 50%;
  border: var(--line-thick) solid var(--border);
  font-weight: 700;
  font-size: 0.95rem;
}
.step--done .step-number {
  border-color: var(--ok);
  color: var(--ok);
}
.step--current {
  color: var(--text);
  font-weight: 600;
}
.step--current .step-number {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
}
.body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0.5rem;
}
</style>
