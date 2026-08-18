<script setup>
// Overlay on top of whatever's currently showing — not a route change, so
// the slideshow keeps running underneath it and Back (remoteNav.js)
// dismisses without navigating anywhere. Mounted at the top level in
// App.vue alongside <router-view>, not nested inside it.
import { nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { menuOpen, closeMenu } from '../menuOverlay.js'
import { shows, pinnedShowId, pinShow } from '../slideshowState.js'

const router = useRouter()
const { t } = useI18n()

async function selectShow(show) {
  // "Main" is always represented as null server-side (see pin-show's
  // contract) rather than its own id, so an explicit re-pin to Main
  // behaves identically to never having pinned anything.
  await pinShow(show.is_main ? null : show.id)
  closeMenu()
}

function openSettings() {
  closeMenu()
  router.push('/settings')
}

// Opening the overlay isn't a route change, so router.afterEach's
// focus-management hook (remoteNav.js) never fires for it — focus the
// first item ourselves whenever it opens.
watch(menuOpen, (open) => {
  if (!open) return
  nextTick(() => {
    document.querySelector('.menu-overlay .list-item')?.focus()
  })
})
</script>

<template>
  <div v-if="menuOpen" class="menu-overlay">
    <div class="menu-card">
      <button
        v-for="show in shows"
        :key="show.id"
        class="list-item"
        :class="{ 'list-item--active': show.id === pinnedShowId }"
        @click="selectShow(show)"
      >
        <span>{{ show.name }}</span>
        <span v-if="show.id === pinnedShowId" class="pill ok">{{ t('menu.playing') }}</span>
      </button>
      <div class="menu-divider" />
      <button class="list-item" @click="openSettings">
        {{ t('menu.settings') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.menu-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.menu-card {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  width: min(30rem, 80vw);
  max-height: 80vh;
  overflow-y: auto;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 1rem;
  padding: 1.5rem;
}
.menu-card .list-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 1rem 1.2rem;
  font-size: 1.1rem;
}
.menu-card .list-item--active {
  border-color: var(--accent);
}
.menu-divider {
  border-top: 1px solid var(--border);
  margin: 0.25rem 0;
}
</style>
