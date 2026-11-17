<script setup>
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'

const { t } = useI18n()

// The product's splash image, if it has one (vite.config.js) — the same one the
// first-run Welcome screen shows.
const hasLogo = __HAS_PRODUCT_SPLASH__

// Set on a branded build only (vite.config.js).
const brand = __BRAND_NAME__
const platform = 'SlideAnnouncer'
const sourceUrl = 'https://github.com/fiforms/slideannouncer'

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

    <!-- Each paragraph is focusable (tabindex) so the remote's Up/Down steps
         through the text a block at a time, scrolling it into view. -->
    <div class="legal">
      <p tabindex="0">{{ brand ? t('settings.about.basedOn', { brand, platform }) : t('settings.about.openSource', { platform }) }}</p>
      <p tabindex="0">{{ t('settings.about.licence', { platform }) }}</p>
      <p tabindex="0">{{ t('settings.about.source') }} <strong class="url">{{ sourceUrl }}</strong></p>
      <p tabindex="0" class="dim">{{ t('settings.about.noWarranty') }}</p>
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
.legal {
  max-width: 38rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  font-size: 1rem;
  line-height: 1.4;
}
.legal p { margin: 0; padding: 0.4rem 0.6rem; border-radius: 0.5rem; }
.legal p:focus-visible { outline: var(--line-thick, 3px) solid var(--accent); outline-offset: 2px; }
.url { overflow-wrap: anywhere; }
.dim { color: var(--text-dim); }
.label { color: var(--text-dim); }
.value { font-weight: 600; }
</style>
