export async function request(path, options) {
  const res = await fetch(path, options)
  let body = null
  try {
    body = await res.json()
  } catch {
    // no JSON body (e.g. a network-level failure page) — fall through
  }
  if (!res.ok) {
    const err = new Error(body?.detail || `${path} failed (${res.status})`)
    // Endpoints that explain themselves (e.g. a failed WiFi join's reason
    // code and log) send more than `detail`; keep it for the caller.
    err.body = body
    throw err
  }
  return body
}

export const api = {  localStatus: () => request('/api/local/status'),
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
  pair: (code, deviceName) =>
    request('/api/local/pair', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, device_name: deviceName }),
    }),
  unpair: () => request('/api/local/unpair', { method: 'POST' }),
  updateCheckStatus: () => request('/api/local/system/update-check'),
  triggerUpdateCheck: () => request('/api/local/system/update-check', { method: 'POST' }),
  triggerUpdateApply: () => request('/api/local/system/update-apply', { method: 'POST' }),
  updateProgress: () => request('/api/local/system/update-progress'),
  audioOutputStatus: () => request('/api/local/audio-output'),
  audioVolumeStatus: () => request('/api/local/audio-volume'),
  setAudioOutput: (value) =>
    request('/api/local/audio-output', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    }),
  screenResolutionStatus: () => request('/api/local/screen-resolution'),
  setScreenResolution: (value) =>
    request('/api/local/screen-resolution', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    }),
  networkDiagnostics: (rescan = false) => request(`/api/local/network/diagnostics${rescan ? '?rescan=true' : ''}`),
  networkServerCheck: () => request('/api/local/network/server-check'),
  networkPortalSignIn: (returnPath) =>
    request('/api/local/network/portal/sign-in', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ return_path: returnPath }),
    }),
  setLanguage: (language) =>
    request('/api/local/language', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ language }),
    }),
  setDeviceName: (deviceName) =>
    request('/api/local/device-name', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_name: deviceName }),
    }),
  completeSetup: () => request('/api/local/setup/complete', { method: 'POST' }),
  reboot: () => request('/api/local/system/reboot', { method: 'POST' }),
  sleepDisplay: () => request('/api/local/system/sleep', { method: 'POST' }),
  factoryReset: () => request('/api/local/system/factory-reset', { method: 'POST' }),
}
