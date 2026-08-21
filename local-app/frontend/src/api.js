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
  pair: (code, deviceName) =>
    request('/api/local/pair', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, device_name: deviceName }),
    }),
  unpair: () => request('/api/local/unpair', { method: 'POST' }),
  syncStatus: () => request('/api/local/sync/status'),
  slideshow: () => request('/api/local/slideshow'),
  pinShow: (showId) =>
    request('/api/local/pin-show', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ show_id: showId }),
    }),
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
  srtSinkStatus: () => request('/api/local/srt-sink'),
  srtSinkPlaying: () => request('/api/local/srt-sink/playing'),
  setSrtSink: (enabled) =>
    request('/api/local/srt-sink', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    }),
  regenerateSrtSinkPassphrase: () => request('/api/local/srt-sink/regenerate', { method: 'POST' }),
  revelationScan: () => request('/api/local/revelation/scan'),
  revelationStatus: () => request('/api/local/revelation/status'),
  revelationPair: (host, port, pin) =>
    request('/api/local/revelation/pair', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ host, port, pin }),
    }),
  revelationUnpair: (instanceId) =>
    request('/api/local/revelation/unpair', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instance_id: instanceId }),
    }),
  revelationDisplaySettings: () => request('/api/local/revelation/display-settings'),
  setRevelationDisplaySettings: (variant, lang) =>
    request('/api/local/revelation/display-settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ variant, lang }),
    }),
  reboot: () => request('/api/local/system/reboot', { method: 'POST' }),
  sleepDisplay: () => request('/api/local/system/sleep', { method: 'POST' }),
  factoryReset: () => request('/api/local/system/factory-reset', { method: 'POST' }),
}
