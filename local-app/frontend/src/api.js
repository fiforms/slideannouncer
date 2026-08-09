async function request(path, options) {
  const res = await fetch(path, options)
  let body = null
  try {
    body = await res.json()
  } catch {
    // no JSON body (e.g. a network-level failure page) — fall through
  }
  if (!res.ok) {
    throw new Error(body?.detail || `${path} failed (${res.status})`)
  }
  return body
}

export const api = {
  localStatus: () => request('/api/local/status'),
  networkStatus: () => request('/api/local/network/status'),
  networkScan: () => request('/api/local/network/scan'),
  networkConnect: (ssid, password) =>
    request('/api/local/network/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ssid, password }),
    }),
  networkForget: (ssid) =>
    request('/api/local/network/forget', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ssid }),
    }),
}
