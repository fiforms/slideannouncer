<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'

const router = useRouter()

const code = ref('')
const deviceName = ref(window.location.hostname || 'Slide Announcer')

// idle -> pairing -> success | error
const state = ref('idle')
const errorMessage = ref(null)

async function pair() {
  state.value = 'pairing'
  errorMessage.value = null
  try {
    await api.pair(code.value.trim(), deviceName.value.trim())
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
    <h1>Pair This Device</h1>

    <form v-if="state === 'idle' || state === 'error'" class="form" @submit.prevent="pair">
      <p class="hint">
        Generate a pairing code from your church's Slide Announcer devices
        page on the AnnouncementSlides website, then enter it below.
      </p>

      <label class="field">
        <span>Device name</span>
        <input type="text" v-model="deviceName" autocomplete="off">
      </label>

      <label class="field">
        <span>Pairing code</span>
        <input
          type="text"
          v-model="code"
          inputmode="numeric"
          maxlength="6"
          autofocus
          autocomplete="off"
          class="code-input"
        >
      </label>

      <p v-if="state === 'error'" class="pill warn">{{ errorMessage }}</p>

      <button type="submit" class="tile action" :disabled="code.length !== 6 || !deviceName">
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
