<script setup>
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { navZone } from '../../remoteNav.js'
import { features, loadFeatures } from '../../features.js'

const { t } = useI18n()
const route = useRoute()

// Smart-TV style settings shell: a left-hand category rail plus a content
// pane, per SLIDE_ANNOUNCER.md's "local settings menu" (Kiosk display).
// Remote navigation between (and within) the two panes is remoteNav.js's
// job — data-nav-zone marks each pane for it, data-rail-path marks the
// category items it browses as focus moves down the rail, and navZone
// says which pane currently has focus so it can be tinted.
//
// Optional features' own pages (`sub`) are listed, indented, under
// Advanced only while switched on there — see features.js.
const categories = computed(() => [
  { path: '/settings/network', label: t('settingsLayout.network') },
  { path: '/settings/pairing', label: t('settingsLayout.pairing') },
  { path: '/settings/system', label: t('settingsLayout.system') },
  { path: '/settings/advanced', label: t('settingsLayout.advanced') },
  features.revelation && { path: '/settings/revelation', label: t('settingsLayout.revelationPeering'), sub: true },
  features.srtSink && { path: '/settings/srt-sink', label: t('settingsLayout.videoReceiver'), sub: true },
].filter(Boolean))

onMounted(loadFeatures)
</script>

<template>
  <div class="settings">
    <aside class="rail pane" :class="{ 'pane--active': navZone === 'rail' }" data-nav-zone="rail">
      <router-link to="/kiosk" class="back-link">{{ t('settingsLayout.backToSlideshow') }}</router-link>
      <nav>
        <router-link
          v-for="cat in categories"
          :key="cat.path"
          :to="cat.path"
          class="rail-item"
          :class="{ 'rail-item--active': route.meta.railPath === cat.path, 'rail-item--sub': cat.sub }"
          :data-rail-path="cat.path"
        >
          {{ cat.label }}
        </router-link>
      </nav>
    </aside>
    <section class="content pane" :class="{ 'pane--active': navZone === 'content' }" data-nav-zone="content">
      <router-view />
    </section>
  </div>
</template>

<style scoped>
.settings {
  display: grid;
  grid-template-columns: 16rem 1fr;
  gap: 1.5rem;
  height: 100%;
}
/* Both panes scroll on their own (focus() scrolls the focused control into
   view) and the one holding focus gets a slightly lighter tint + accent
   edge, so it's obvious at a glance whether arrows are walking the rail
   or the page. */
.pane {
  min-height: 0;
  overflow-y: auto;
  padding: 1.25rem;
  border-radius: 1rem;
  border: var(--line) solid transparent;
  transition: background-color 0.15s, border-color 0.15s;
}
.pane--active {
  background: var(--zone-active);
  border-color: var(--zone-active-border);
}
.rail {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}
.back-link {
  color: var(--text-dim);
  text-decoration: none;
  font-size: 0.95rem;
}
.back-link:hover,
.back-link:focus-visible {
  color: var(--text);
}
nav {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.rail-item {
  display: block;
  padding: 0.9rem 1.2rem;
  border-radius: 0.6rem;
  color: var(--text);
  text-decoration: none;
  font-size: 1.05rem;
  border: var(--line) solid transparent;
}
.rail-item:hover,
.rail-item:focus-visible {
  background: var(--panel-hover);
}
.rail-item--sub {
  margin-left: 1.5rem;
  padding: 0.65rem 1rem;
  font-size: 0.95rem;
}
.rail-item--active {
  background: var(--panel);
  border-color: var(--accent);
  font-weight: 600;
}
/* Rail dims while you're working in the content pane — the selected
   category stays marked, just quieter. */
.rail:not(.pane--active) .rail-item,
.rail:not(.pane--active) .back-link {
  color: var(--text-dim);
}
.rail:not(.pane--active) .rail-item--active {
  border-color: var(--border);
}
.content {
  min-width: 0;
}
</style>
