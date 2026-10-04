// Runtime for overlay widgets on the kiosk — the device-side twin of the
// main app's resources/js/Composables/widgets/widgetHost.js, implementing
// the same contract (see the main app's resources/widgets/README.md):
//
//     export function mount(el, { width, height, params, api }) {
//         …draw into el, in a width × height coordinate space…
//         return () => { …stop timers… };   // cleanup
//     }
//
// Differences from the web host: widget code comes from the device's own
// mirror (/media/widgets/…, see backend/widgets.py), and api.fetch() goes
// to the local backend's /api/local/widget-data proxy, which forwards to
// the server by reference and serves the last good answer while offline.
//
// Readiness works as in the web host: a widget is "ready" when `mount`
// returns, unless its module exports `manualReady = true`, in which case it
// calls `api.ready()` once its first real content is drawn. A failing or
// silent widget counts as ready after READY_TIMEOUT_MS.

export class WidgetDataError extends Error {
  constructor(reason, status) {
    super(`Widget data unavailable: ${reason}`)
    this.reason = reason
    this.status = status
  }
}

function storageFor(prefix) {
  const key = (k) => `${prefix}${k}`
  return {
    get(k) {
      try {
        const raw = localStorage.getItem(key(k))
        return raw === null ? null : JSON.parse(raw)
      } catch {
        return null
      }
    },
    set(k, value) {
      try { localStorage.setItem(key(k), JSON.stringify(value)) } catch { /* storage unavailable */ }
    },
    remove(k) {
      try { localStorage.removeItem(key(k)) } catch { /* storage unavailable */ }
    },
  }
}

function createApi(placement, locale, location, ready) {
  return Object.freeze({
    mode: 'live',
    locale,
    // Where this screen is: { name, latitude, longitude, source } — its
    // church's, else the server's site default, else null.
    location: location ? Object.freeze({ ...location }) : null,
    // fetch(endpoint, args?) resolves to { data, fetched_at, stale };
    // rejects with WidgetDataError. Runtime args (e.g. a forecast's
    // lat/lon) travel as ?args[name]=value and are checked server-side.
    async fetch(endpoint, args) {
      const query = new URLSearchParams()
      for (const [k, v] of Object.entries(args ?? {})) query.append(`args[${k}]`, String(v))
      const base = placement.data_url.replace('__endpoint__', encodeURIComponent(endpoint))
      const url = query.toString() ? `${base}?${query}` : base
      const res = await fetch(url, { headers: { Accept: 'application/json' } })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) throw new WidgetDataError(body.error ?? 'unavailable', res.status)
      return body
    },
    storage: storageFor(`as-widget:${placement.widget}:${placement.id}:`),
    // Call once the first real content is painted (widgets that export
    // `manualReady = true`; harmless otherwise). Idempotent.
    ready,
  })
}

const READY_TIMEOUT_MS = 8000

// Mounts one placement into `el`. Returns { ready, dispose }: `ready`
// resolves (never rejects) once the widget has painted — or failed, or timed
// out — and `dispose` runs the cleanup. Safe to call dispose before the
// module has finished loading.
export function mountWidget(el, placement, locale, location = null) {
  let disposed = false
  let cleanup = null
  let markReady
  const ready = new Promise((resolve) => { markReady = resolve })
  const timeout = setTimeout(() => {
    console.warn(`Widget "${placement.widget}" did not signal ready in ${READY_TIMEOUT_MS}ms`)
    markReady()
  }, READY_TIMEOUT_MS)
  ready.then(() => clearTimeout(timeout))

  function runCleanup() {
    const fn = cleanup
    cleanup = null
    try { fn?.() } catch (err) { console.warn(`Widget "${placement.widget}" cleanup failed`, err) }
    el.replaceChildren()
  }

  ;(async () => {
    const mod = await import(/* @vite-ignore */ placement.entry_url)
    if (disposed) return
    const result = await mod.mount(el, {
      width: placement.w,
      height: placement.h,
      params: Object.freeze({ ...(placement.params ?? {}) }),
      api: createApi(placement, locale, location, markReady),
    })
    cleanup = typeof result === 'function' ? result : result?.destroy?.bind(result) ?? null
    if (disposed) runCleanup()
    if (mod.manualReady !== true) markReady()
  })().catch((err) => {
    console.warn(`Widget "${placement.widget}" failed to load`, err)
    markReady()
  })

  return {
    ready,
    dispose() {
      disposed = true
      markReady()
      runCleanup()
    },
  }
}
