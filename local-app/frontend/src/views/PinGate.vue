<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api.js'
import { unlock } from '../pinLock.js'

// Full-screen PIN gate in front of Settings — a low-effort deterrent
// against someone grabbing the remote/keyboard and messing with settings,
// not real security (see SLIDE_ANNOUNCER.md, "Kiosk display" > Settings
// PIN). The router guard in router.js sends us here whenever it's about
// to enter /settings with a settings_pin configured and this session not
// yet unlocked; ?redirect is the settings path that was actually
// requested. Failing to enter the right PIN within TIMEOUT_SECONDS bounces
// straight back to the kiosk, per that design.
const TIMEOUT_SECONDS = 15

const router = useRouter()
const route = useRoute()

const pin = ref(null)
const entered = ref('')
const wrongPulse = ref(false)
const secondsLeft = ref(TIMEOUT_SECONDS)

let countdownTimer = null

const dotCount = computed(() => pin.value?.length ?? 4)

function goToSettings() {
  router.replace(route.query.redirect || '/settings/system')
}

function revertToKiosk() {
  router.replace('/kiosk')
}

function checkPin() {
  if (entered.value === pin.value) {
    unlock()
    goToSettings()
    return
  }
  wrongPulse.value = true
  entered.value = ''
  setTimeout(() => { wrongPulse.value = false }, 400)
}

function pressDigit(digit) {
  if (!pin.value || entered.value.length >= pin.value.length) return
  entered.value += digit
  if (entered.value.length === pin.value.length) checkPin()
}

function pressBackspace() {
  entered.value = entered.value.slice(0, -1)
}

function onKeydown(event) {
  if (event.key.length === 1 && event.key >= '0' && event.key <= '9') {
    event.preventDefault()
    pressDigit(event.key)
  } else if (event.key === 'Backspace') {
    event.preventDefault()
    pressBackspace()
  }
}

onMounted(async () => {
  try {
    const data = await api.slideshow()
    pin.value = data.settings?.settings_pin || null
  } catch {
    pin.value = null
  }

  // No PIN configured by the time we actually get here (cleared server-side
  // moments ago, or the router guard's own check failed open) — nothing to
  // gate, so just continue on to Settings rather than stranding the user.
  if (!pin.value) {
    unlock()
    goToSettings()
    return
  }

  window.addEventListener('keydown', onKeydown)
  countdownTimer = setInterval(() => {
    secondsLeft.value -= 1
    if (secondsLeft.value <= 0) revertToKiosk()
  }, 1000)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  if (countdownTimer) clearInterval(countdownTimer)
})
</script>

<template>
  <div v-if="pin" class="gate">
    <p class="prompt">Enter Settings PIN</p>

    <div class="dots" :class="{ 'dots--error': wrongPulse }">
      <span v-for="i in dotCount" :key="i" class="dot" :class="{ 'dot--filled': i <= entered.length }" />
    </div>

    <p class="countdown">Returning to slideshow in {{ secondsLeft }}s&hellip;</p>

    <div class="keypad">
      <button v-for="n in 9" :key="n" class="tile key" @click="pressDigit(String(n))">{{ n }}</button>
      <button class="tile key key--muted" @click="revertToKiosk">Cancel</button>
      <button class="tile key" @click="pressDigit('0')">0</button>
      <button class="tile key key--muted" @click="pressBackspace">&#9003;</button>
    </div>
  </div>
</template>

<style scoped>
.gate {
  position: fixed;
  inset: 0;
  background: var(--bg);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1.5rem;
}
.prompt {
  font-size: 1.4rem;
  color: var(--text);
  margin: 0;
}
.dots {
  display: flex;
  gap: 0.9rem;
}
.dot {
  width: 1.1rem;
  height: 1.1rem;
  border-radius: 50%;
  border: 2px solid var(--border);
  background: transparent;
}
.dot--filled {
  background: var(--accent);
  border-color: var(--accent);
}
.dots--error .dot--filled {
  background: var(--danger);
  border-color: var(--danger);
}
.countdown {
  color: var(--text-dim);
  font-size: 0.95rem;
  margin: 0;
}
.keypad {
  display: grid;
  grid-template-columns: repeat(3, 5rem);
  gap: 0.75rem;
}
.key {
  height: 4rem;
  font-size: 1.4rem;
  text-align: center;
  display: flex;
  align-items: center;
  justify-content: center;
}
.key--muted {
  font-size: 1rem;
  color: var(--text-dim);
}
</style>
