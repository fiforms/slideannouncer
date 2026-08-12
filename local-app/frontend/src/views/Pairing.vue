<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'

const router = useRouter()

// Optional field — a church buying several of these has no reason to name
// each one before pairing it, and window.location.hostname (the old
// default) is just "localhost" when this page loads over the local nginx
// proxy, which is worse than no default at all. A random suffix keeps
// several freshly-unboxed devices from all defaulting to the exact same
// name if left untouched.
function randomDeviceName() {
  const suffix = Math.floor(1000 + Math.random() * 9000)
  return `SlideAnnouncer-${suffix}`
}

const code = ref('')
const deviceName = ref(randomDeviceName())

const nameInput = ref(null)
const codeInput = ref(null)

// idle -> pairing -> success | error
const state = ref('idle')
const errorMessage = ref(null)

async function pair() {
  if (code.value.length !== 6) return
  state.value = 'pairing'
  errorMessage.value = null
  try {
    await api.pair(code.value.trim(), deviceName.value.trim() || randomDeviceName())
    state.value = 'success'
  } catch (err) {
    errorMessage.value = err.message
    state.value = 'error'
  }
}

function done() {
  router.push('/')
}
</script>

<template>
  <div class="card">
    <router-link v-if="state === 'idle' || state === 'error'" to="/settings" class="back-link">
      &larr; Settings (connect WiFi, etc.)
    </router-link>

    <h1>Pair This Device</h1>

    <form v-if="state === 'idle' || state === 'error'" class="form" @submit.prevent="pair">
      <p class="hint">
        Generate a pairing code from your church's Slide Announcer devices
        page on the AnnouncementSlides website, then enter it below.
      </p>

      <label class="field">
        <span>Device Name or Location <span class="optional">(optional)</span></span>
        <input
          type="text"
          v-model="deviceName"
          ref="nameInput"
          autocomplete="off"
          @keydown.enter.prevent="codeInput?.focus()"
        >
      </label>

      <label class="field">
        <span>Pairing code</span>
        <input
          type="text"
          v-model="code"
          ref="codeInput"
          inputmode="numeric"
          maxlength="6"
          autofocus
          autocomplete="off"
          class="code-input"
          @keydown.enter.prevent="pair()"
        >
      </label>

      <p v-if="state === 'error'" class="pill warn">{{ errorMessage }}</p>

      <button type="submit" class="tile action" :disabled="code.length !== 6">
        Pair
      </button>
    </form>

    <div v-else-if="state === 'pairing'" class="status-block">
      <p>Pairing…</p>
    </div>

    <div v-else-if="state === 'success'" class="status-block">
      <p class="pill ok">Paired</p>
      <button class="tile action" @click="done">Done</button>
    </div>
  </div>
</template>

<style scoped>
.card {
  max-width: 28rem;
  margin: 6vh auto 0;
}
.back-link {
  display: inline-block;
  margin-bottom: 1rem;
  color: var(--text-dim);
  text-decoration: none;
  font-size: 0.95rem;
}
.back-link:hover,
.back-link:focus-visible {
  color: var(--text);
}
h1 { text-align: center; margin-bottom: 1.5rem; }
.form {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.hint { color: var(--text-dim); margin: 0; }
.optional { color: var(--text-dim); font-weight: normal; }
.code-input {
  font-size: 1.75rem;
  letter-spacing: 0.4em;
  text-align: center;
}
.action {
  align-self: center;
  padding: 0.9rem 2.2rem;
  font-size: 1.05rem;
}
.action:disabled { opacity: 0.5; cursor: default; }
.status-block {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  align-items: center;
}
</style>
