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
  updateCheckStatus: () => request('/api/local/system/update-check'),
  triggerUpdateCheck: () => request('/api/local/system/update-check', { method: 'POST' }),
  reboot: () => request('/api/local/system/reboot', { method: 'POST' }),
  factoryReset: () => request('/api/local/system/factory-reset', { method: 'POST' }),
}
