<script setup>
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'

const { t } = useI18n()

// The product's splash image, if it has one (vite.config.js) — the same one the
// first-run Welcome screen shows.
const hasLogo = __HAS_PRODUCT_SPLASH__

const status = ref(null)

onMounted(async () => {
  status.value = await api.localStatus().catch(() => null) // leave the rows as dashes rather than erroring
})
</script>

<template>
  <div class="about">
    <!-- mix-blend-mode: a splash is usually on black; lighten lets the pane's own
         colour show through instead of a black box (as on the Welcome screen). -->
    <img v-if="hasLogo" class="logo" src="/product-splash.png" :alt="t('settings.about.title')">
    <h1 v-else class="title">{{ t('settings.about.title') }}</h1>

    <div class="info-grid">
      <span class="label">{{ t('settings.system.currentOsVersion') }}</span>
      <span class="value">{{ status?.image_version || '—' }}</span>
      <span class="label">{{ t('settings.system.currentAppVersion') }}</span>
      <span class="value">{{ status?.app_version || '—' }}</span>
      <span class="label">{{ t('settings.network.hostname') }}</span>
      <span class="value">{{ status?.hostname || '—' }}</span>
    </div>
  </div>
</template>

<style scoped>
.about {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1.2rem;
  text-align: center;
}
.title { font-size: 2.8rem; margin: 0; }
.logo {
  display: block;
  max-width: min(100%, 36rem);
  max-height: 34vh;
  object-fit: contain;
  mix-blend-mode: lighten;
}
.info-grid {
  display: grid;
  grid-template-columns: auto auto;
  gap: 0.5rem 1.5rem;
  text-align: left;
  font-size: 1.15rem;
}
.label { color: var(--text-dim); }
.value { font-weight: 600; }
</style>
