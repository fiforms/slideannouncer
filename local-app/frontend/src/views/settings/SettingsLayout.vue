<script setup>
// Smart-TV style settings shell: a left-hand category rail plus a content
// pane, per SLIDE_ANNOUNCER.md's "local settings menu" (Kiosk display).
// The device's remote is a keyboard+pointer HID combo (see
// provisioning/README.md), so plain focusable links/buttons with visible
// focus rings are enough navigation — no custom spatial-nav system needed.
const categories = [
  { path: '/settings/network', label: 'Network' },
  { path: '/settings/system', label: 'System' },
  { path: '/settings/about', label: 'About' },
]
</script>

<template>
  <div class="settings">
    <aside class="rail">
      <router-link to="/" class="back-link">&larr; Back to home</router-link>
      <nav>
        <router-link
          v-for="cat in categories"
          :key="cat.path"
          :to="cat.path"
          class="rail-item"
          active-class="rail-item--active"
        >
          {{ cat.label }}
        </router-link>
      </nav>
    </aside>
    <section class="content">
      <router-view />
    </section>
  </div>
</template>

<style scoped>
.settings {
  display: grid;
  grid-template-columns: 16rem 1fr;
  gap: 2rem;
  height: 100%;
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
  border: 1px solid transparent;
}
.rail-item:hover,
.rail-item:focus-visible {
  background: var(--panel-hover);
}
.rail-item--active {
  background: var(--panel);
  border-color: var(--accent);
  font-weight: 600;
}
.content {
  min-width: 0;
}
</style>
