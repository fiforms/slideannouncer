<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api.js'

const props = defineProps({ ssid: { type: String, required: true } })
const route = useRoute()
const router = useRouter()

const secured = route.query.secured !== '0'
const password = ref('')
const showPassword = ref(false)

// idle -> connecting -> success | error
const state = ref('idle')
const errorMessage = ref(null)
const connectivity = ref(null)

async function connect() {
  state.value = 'connecting'
  errorMessage.value = null
  try {
    const result = await api.networkConnect(props.ssid, secured ? password.value : null)
    connectivity.value = result.connectivity
    state.value = 'success'
  } catch (err) {
    errorMessage.value = err.message
    state.value = 'error'
  }
}

function done() {
  router.push('/settings/network')
}
</script>

<template>
  <div>
    <h1>{{ ssid }}</h1>

    <form v-if="state === 'idle' || state === 'error'" class="form" @submit.prevent="connect">
      <label v-if="secured" class="field">
        <span>Password</span>
        <div class="password-row">
          <input
            :type="showPassword ? 'text' : 'password'"
            v-model="password"
            autofocus
            autocomplete="off"
          >
          <button type="button" class="tile toggle" @click="showPassword = !showPassword">
            {{ showPassword ? 'Hide' : 'Show' }}
          </button>
        </div>
      </label>
      <p v-else class="hint">This network is open — no password needed.</p>

      <p v-if="state === 'error'" class="pill warn">{{ errorMessage }}</p>

      <button type="submit" class="tile action" :disabled="secured && !password">
        Connect
      </button>
    </form>

    <div v-else-if="state === 'connecting'" class="status-block">
      <p>Connecting to {{ ssid }}…</p>
    </div>

    <div v-else-if="state === 'success'" class="status-block">
      <p class="pill ok">Connected</p>
      <p class="hint">Connectivity check: {{ connectivity }}</p>
      <button class="tile action" @click="done">Done</button>
    </div>
  </div>
</template>

<style scoped>
h1 { margin-top: 0; word-break: break-word; }
.form {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  max-width: 28rem;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.password-row {
  display: flex;
  gap: 0.5rem;
}
.password-row input { flex: 1; }
.toggle { padding: 0.6rem 1rem; }
.hint { color: var(--text-dim); }
.action {
  align-self: flex-start;
  padding: 0.9rem 1.8rem;
  font-size: 1.05rem;
}
.action:disabled { opacity: 0.5; cursor: default; }
.status-block {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  align-items: flex-start;
}
</style>
