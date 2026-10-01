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

function createApi(placement, locale) {
  return Object.freeze({
    mode: 'live',
    locale,
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
  })
}

// Mounts one placement into `el`. Returns { dispose } — safe to call
// before the module has finished loading.
export function mountWidget(el, placement, locale) {
  let disposed = false
  let cleanup = null

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
      api: createApi(placement, locale),
    })
    cleanup = typeof result === 'function' ? result : result?.destroy?.bind(result) ?? null
    if (disposed) runCleanup()
  })().catch((err) => {
    console.warn(`Widget "${placement.widget}" failed to load`, err)
  })

  return {
    dispose() {
      disposed = true
      runCleanup()
    },
  }
}
