<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { openCaptivePortal } from '../../captivePortal.js'

const route = useRoute()
const { t, te } = useI18n()
const networkBase = route.meta.networkBase || '/settings/network'

const report = ref(null)
const error = ref(null)
const loading = ref(false)
const portalError = ref(null)

// Findings are always open; the detail sections start collapsed — each
// header is a focusable button, which is also how the remote scrolls
// through a long read-only page.
const open = reactive({ radio: false, path: false, nearby: false, profiles: false, attempts: false, log: false })

async function load(rescan = false) {
  loading.value = true
  error.value = null
  try {
    report.value = await api.networkDiagnostics(rescan)
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

onMounted(() => load())

async function signIn() {
  portalError.value = null
  try {
    await openCaptivePortal(`${networkBase}/diagnostics`)
  } catch (err) {
    portalError.value = err.message
  }
}

function reasonText(reason) {
  const key = `settings.wifiConnect.failure.${reason}`
  return te(key) ? t(key) : reason
}

function finding(f) {
  const key = `settings.diagnostics.finding.${f.code}`
  const p = { ...f.params }
  for (const k of ['names']) if (Array.isArray(p[k])) p[k] = p[k].join(', ')
  if (p.reason) p.reason = reasonText(p.reason)
  for (const k of Object.keys(p)) if (p[k] == null) p[k] = '—'
  return te(key) ? t(key, p) : f.code
}

function kindLabel(kind) {
  const key = `settings.wifiList.kind.${kind}`
  return te(key) ? t(key) : kind
}

const status = computed(() => report.value?.status)
const path = computed(() => report.value?.path)
const system = computed(() => report.value?.system || {})
const radio = computed(() => report.value?.radio || {})

// One OK/fail/unknown pill for a path step.
function stepClass(ok) {
  return ok === true ? 'ok' : ok === false ? 'warn' : ''
}

const steps = computed(() => {
  const p = path.value
  if (!p || p.error) return []
  const rows = []
  if (p.gateway) {
    rows.push({
      key: 'gateway', ok: p.gateway.ok,
      detail: [p.gateway.host, p.gateway.avg_ms != null && `${p.gateway.avg_ms.toFixed(0)} ms`,
        p.gateway.loss_percent > 0 && t('settings.diagnostics.loss', { pct: p.gateway.loss_percent })].filter(Boolean).join(' · '),
    })
  }
  rows.push({
    key: 'internetIp', ok: p.internet_ip?.ok,
    detail: [p.internet_ip?.host, p.internet_ip?.avg_ms != null && `${p.internet_ip.avg_ms.toFixed(0)} ms`].filter(Boolean).join(' · '),
  })
  rows.push({
    key: 'dns', ok: p.dns?.ok,
    detail: p.dns?.ok ? `${p.dns.host} → ${p.dns.addresses?.[0] ?? ''} (${p.dns.elapsed_ms} ms)` : `${p.dns?.host ?? ''} ${p.dns?.error ?? ''}`,
  })
  rows.push({
    key: 'http', ok: p.http?.state === 'full' ? true : p.http?.state === 'portal' ? false : p.http?.state ? false : null,
    detail: p.http?.state === 'portal'
      ? t('settings.diagnostics.httpPortal', { status: p.http.http_status ?? '?' })
      : p.http?.state === 'none' ? (p.http.error || '') : `HTTP ${p.http?.http_status ?? ''}`,
  })
  if (p.server) {
    rows.push({
      key: 'server', ok: p.server.state === 'ok' ? true : p.server.state === 'unconfigured' ? null : false,
      detail: [p.server.host, p.server.latency_ms != null && `${p.server.latency_ms} ms`, p.server.state !== 'ok' && p.server.detail]
        .filter(Boolean).join(' · '),
    })
  }
  return rows
})

function dbmSummary(link) {
  return [link.signal_dbm, link.band, link.tx_bitrate && `↑ ${link.tx_bitrate.split(' HE')[0].split(' VHT')[0]}`].filter(Boolean).join(' · ')
}
</script>

<template>
  <div class="settings-page">
    <div class="head">
      <h1>{{ t('settings.diagnostics.title') }}</h1>
      <div class="head-actions">
        <button class="tile action" :disabled="loading" @click="load(false)">
          {{ loading ? t('settings.diagnostics.running') : t('settings.diagnostics.rerun') }}
        </button>
        <button class="tile action" :disabled="loading" @click="load(true)">{{ t('settings.diagnostics.rescan') }}</button>
      </div>
    </div>

    <p v-if="error" class="pill warn">{{ error }}</p>
    <p v-else-if="!report" class="hint">{{ t('settings.diagnostics.running') }}</p>

    <template v-if="report">
      <!-- Summary: what's wrong, in words, worst first. -->
      <section class="tile panel">
        <div class="panel-title">
          <h2>{{ t('settings.diagnostics.summary') }}</h2>
          <span class="label">{{ report.generated_at }}</span>
        </div>
        <p v-if="!report.findings.length" class="pill ok">{{ t('settings.diagnostics.allGood') }}</p>
        <ul v-else class="findings">
          <li v-for="f in report.findings" :key="f.code" :class="f.severity">
            <span class="pill" :class="f.severity === 'info' ? '' : 'warn'">{{ t(`settings.diagnostics.severity.${f.severity}`) }}</span>
            <span>{{ finding(f) }}</span>
          </li>
        </ul>
        <div class="info-grid">
          <span class="label">{{ t('settings.network.connection') }}</span>
          <span class="value">
            <span class="pill" :class="status.connected ? 'ok' : 'warn'">
              {{ status.connected ? `${status.connection_type}${status.ssid ? ` — ${status.ssid}` : ''}` : t('settings.network.disconnected') }}
            </span>
          </span>
          <template v-if="status.connected">
            <span class="label">{{ t('settings.network.internet') }}</span>
            <span class="value">{{ t(`settings.network.connectivity.${status.connectivity}`) }}</span>
            <span class="label">{{ t('settings.network.ipAddress') }}</span>
            <span class="value">{{ status.ip_addresses?.join(', ') || '—' }}</span>
            <span class="label">{{ t('settings.network.defaultGateway') }}</span>
            <span class="value">{{ status.gateway || '—' }}</span>
            <span class="label">{{ t('settings.network.dnsServer', status.dns_servers?.length || 1) }}</span>
            <span class="value">{{ status.dns_servers?.join(', ') || '—' }}</span>
          </template>
        </div>
        <div v-if="status.connectivity === 'portal' || status.connectivity === 'limited'" class="actions">
          <button class="tile action primary" @click="signIn">{{ t('settings.network.portalSignIn') }}</button>
        </div>
        <p v-if="portalError" class="pill warn">{{ portalError }}</p>
      </section>

      <!-- Radio + driver -->
      <button class="tile section-toggle" :class="{ active: open.radio }" @click="open.radio = !open.radio">
        <span>{{ t('settings.diagnostics.radio') }}</span><span>{{ open.radio ? '▾' : '▸' }}</span>
      </button>
      <section v-if="open.radio" class="tile panel">
        <p v-if="radio.error" class="pill warn">{{ radio.error }}</p>
        <div class="info-grid">
          <template v-if="radio.present">
            <span class="label">{{ t('settings.diagnostics.adapter') }}</span>
            <span class="value">{{ radio.device }} · {{ radio.driver || '?' }} · {{ radio.mac }}</span>
            <span class="label">{{ t('settings.diagnostics.firmware') }}</span>
            <span class="value">{{ radio.firmware || '—' }}</span>
            <span class="label">{{ t('settings.diagnostics.state') }}</span>
            <span class="value">{{ radio.state }} ({{ radio.reason }})</span>
            <span class="label">{{ t('settings.diagnostics.bands') }}</span>
            <span class="value">{{ ['2ghz', '5ghz', '6ghz'].filter((b) => radio.supports?.[b]).map((b) => b.replace('ghz', ' GHz')).join(', ') || '—' }}</span>
            <template v-if="radio.link?.ssid">
              <span class="label">{{ t('settings.diagnostics.link') }}</span>
              <span class="value">{{ dbmSummary(radio.link) }}<template v-if="radio.channel_width_mhz"> · {{ radio.channel_width_mhz }} MHz</template></span>
              <span class="label">{{ t('settings.diagnostics.accessPoint') }}</span>
              <span class="value">{{ radio.link.bssid }}</span>
            </template>
          </template>
          <span class="label">{{ t('settings.diagnostics.country') }}</span>
          <span class="value">{{ system.regulatory_country || '—' }}</span>
          <span class="label">{{ t('settings.diagnostics.rfkill') }}</span>
          <span class="value">{{ system.rfkill_blocked?.length ? t('settings.diagnostics.blocked') : t('settings.diagnostics.unblocked') }}</span>
          <span class="label">NetworkManager</span>
          <span class="value">{{ system.networkmanager || '—' }}</span>
          <span class="label">wpa_supplicant</span>
          <span class="value">{{ system.wpa_supplicant || '—' }}</span>
          <span class="label">{{ t('settings.diagnostics.wpa3') }}</span>
          <span class="value">
            <span class="pill" :class="system.wpa3_software ? 'ok' : 'warn'">
              {{ system.wpa3_software ? t('settings.diagnostics.supported') : t('settings.diagnostics.notSupported') }}
            </span>
          </span>
          <span class="label">{{ t('settings.diagnostics.clock') }}</span>
          <span class="value">
            {{ system.clock }}
            <span v-if="system.ntp_synchronized !== null && system.ntp_synchronized !== undefined" class="pill" :class="system.ntp_synchronized ? 'ok' : 'warn'">
              {{ system.ntp_synchronized ? t('settings.diagnostics.synced') : t('settings.diagnostics.notSynced') }}
            </span>
          </span>
        </div>
      </section>

      <!-- Path out -->
      <button class="tile section-toggle" :class="{ active: open.path }" @click="open.path = !open.path">
        <span>{{ t('settings.diagnostics.path') }}</span><span>{{ open.path ? '▾' : '▸' }}</span>
      </button>
      <section v-if="open.path" class="tile panel">
        <p v-if="!path" class="hint">{{ t('settings.diagnostics.notConnected') }}</p>
        <p v-else-if="path.error" class="pill warn">{{ path.error }}</p>
        <div v-else class="info-grid">
          <template v-for="s in steps" :key="s.key">
            <span class="label">{{ t(`settings.diagnostics.step.${s.key}`) }}</span>
            <span class="value">
              <span class="pill" :class="stepClass(s.ok)">{{ s.ok === true ? 'OK' : s.ok === false ? t('settings.diagnostics.fail') : '—' }}</span>
              {{ s.detail }}
            </span>
          </template>
        </div>
      </section>

      <!-- Nearby -->
      <button class="tile section-toggle" :class="{ active: open.nearby }" @click="open.nearby = !open.nearby">
        <span>{{ t('settings.diagnostics.nearby') }} ({{ report.nearby?.networks?.length ?? 0 }})</span><span>{{ open.nearby ? '▾' : '▸' }}</span>
      </button>
      <section v-if="open.nearby" class="tile panel">
        <p v-if="report.nearby?.error" class="pill warn">{{ report.nearby.error }}</p>
        <table v-else class="grid">
          <thead>
            <tr>
              <th>{{ t('settings.diagnostics.ssid') }}</th><th>{{ t('settings.diagnostics.security') }}</th>
              <th>{{ t('settings.network.signal') }}</th><th>{{ t('settings.diagnostics.channels') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="n in report.nearby.networks" :key="n.ssid" tabindex="0">
              <td>{{ n.ssid }}<span v-if="n.in_use" class="pill ok">{{ t('settings.wifiList.connected') }}</span></td>
              <td>{{ kindLabel(n.kind) }}</td>
              <td>{{ n.signal }}%</td>
              <td>{{ n.access_points.map((a) => `${a.channel ?? '?'}${a.band ? ` (${a.band})` : ''}`).join(', ') }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="report.nearby?.hidden_count" class="hint">{{ t('settings.diagnostics.hidden', { n: report.nearby.hidden_count }) }}</p>
      </section>

      <!-- Saved profiles -->
      <button class="tile section-toggle" :class="{ active: open.profiles }" @click="open.profiles = !open.profiles">
        <span>{{ t('settings.diagnostics.profiles') }} ({{ report.profiles?.length ?? 0 }})</span><span>{{ open.profiles ? '▾' : '▸' }}</span>
      </button>
      <section v-if="open.profiles" class="tile panel">
        <p v-if="!report.profiles?.length" class="hint">{{ t('settings.diagnostics.noProfiles') }}</p>
        <table v-else class="grid">
          <thead><tr><th>{{ t('settings.diagnostics.ssid') }}</th><th>{{ t('settings.diagnostics.keyMgmt') }}</th><th>{{ t('settings.diagnostics.autoconnect') }}</th></tr></thead>
          <tbody>
            <tr v-for="p in report.profiles" :key="p.uuid" tabindex="0">
              <td>{{ p.name }}</td>
              <td>
                {{ p.key_mgmt || t('settings.wifiList.kind.open') }}
                <span v-if="p.broken" class="pill warn">{{ t('settings.diagnostics.broken') }}</span>
              </td>
              <td>{{ p.autoconnect ? t('settings.diagnostics.yes') : t('settings.diagnostics.no') }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- Recent connect attempts -->
      <button class="tile section-toggle" :class="{ active: open.attempts }" @click="open.attempts = !open.attempts">
        <span>{{ t('settings.diagnostics.attempts') }} ({{ report.attempts.length }})</span><span>{{ open.attempts ? '▾' : '▸' }}</span>
      </button>
      <section v-if="open.attempts" class="tile panel">
        <p v-if="!report.attempts.length" class="hint">{{ t('settings.diagnostics.noAttempts') }}</p>
        <ul v-else class="attempts">
          <li v-for="(a, i) in [...report.attempts].reverse()" :key="i" tabindex="0">
            <span class="pill" :class="a.ok ? 'ok' : 'warn'">{{ a.ok ? 'OK' : t('settings.diagnostics.fail') }}</span>
            <strong>{{ a.ssid }}</strong> <span class="hint">{{ a.at }} · {{ kindLabel(a.security_kind) }}</span>
            <div v-if="!a.ok" class="hint">{{ reasonText(a.reason) }}<br><code>{{ a.raw }}</code></div>
          </li>
        </ul>
      </section>

      <!-- Journal -->
      <button class="tile section-toggle" :class="{ active: open.log }" @click="open.log = !open.log">
        <span>{{ t('settings.diagnostics.log') }}</span><span>{{ open.log ? '▾' : '▸' }}</span>
      </button>
      <section v-if="open.log" class="tile panel">
        <p v-if="report.log?.restricted" class="hint">{{ t('settings.diagnostics.logRestricted') }}</p>
        <p v-else-if="!report.log?.available" class="hint">{{ t('settings.diagnostics.logUnavailable') }}</p>
        <p v-else-if="!report.log.lines.length" class="hint">{{ t('settings.diagnostics.logEmpty') }}</p>
        <pre v-else class="log" tabindex="0">{{ report.log.lines.slice(-30).reverse().join('\n') }}</pre>
      </section>
    </template>
  </div>
</template>

<style scoped>
h1 { margin: 0; }
.head { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
.head-actions, .actions { display: flex; gap: 0.75rem; flex-wrap: wrap; }
.action { padding: 0.6rem 1.2rem; font-size: 1rem; }
.action.primary { border-color: var(--accent); background: var(--accent); color: #fff; font-weight: 600; }
.hint { color: var(--text-dim); margin: 0.4rem 0; }
.info-grid { display: grid; grid-template-columns: auto 1fr; gap: 0.35rem 1.5rem; margin: 0.75rem 0; }
.label { color: var(--text-dim); font-weight: 600; }
.value { overflow-wrap: anywhere; }
.findings { list-style: none; margin: 0 0 0.5rem; padding: 0; display: flex; flex-direction: column; gap: 0.6rem; }
.findings li { display: flex; gap: 0.75rem; align-items: baseline; }
.findings .pill { flex: none; }
.section-toggle {
  display: flex; justify-content: space-between; align-items: center;
  width: 100%; padding: 0.8rem 1.2rem; margin-top: 0.75rem; font-size: 1.05rem; text-align: left;
}
.section-toggle.active { border-color: var(--accent); }
.grid { width: 100%; border-collapse: collapse; }
.grid th { text-align: left; color: var(--text-dim); font-weight: 600; padding: 0.25rem 0.75rem 0.25rem 0; }
.grid td { padding: 0.25rem 0.75rem 0.25rem 0; overflow-wrap: anywhere; }
.attempts { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.6rem; }
.attempts code, .log { font-size: 0.8rem; overflow-wrap: anywhere; }
.log { margin: 0; white-space: pre-wrap; }
.grid tr:focus-visible, .attempts li:focus-visible, .log:focus-visible { outline: var(--line-thick, 3px) solid var(--accent); outline-offset: 2px; }
</style>
