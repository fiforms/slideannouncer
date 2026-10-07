// The slideannouncer product's API calls (slideshow, pinned show, LAN video
// receiver, Revelation peering) layered on the core ones, so product code
// imports one `api` and gets both.
import { api as coreApi, request } from '@core/api.js'

export const api = {
  ...coreApi,
  syncStatus: () => request('/api/local/sync/status'),
  slideshow: () => request('/api/local/slideshow'),
  pinShow: (showId) =>
    request('/api/local/pin-show', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ show_id: showId }),
    }),
  srtSinkStatus: () => request('/api/local/srt-sink'),
  srtSinkPlaying: () => request('/api/local/srt-sink/playing'),
  setSrtSink: (enabled) =>
    request('/api/local/srt-sink', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    }),
  setSrtSinkSettings: (changes) =>
    request('/api/local/srt-sink/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(changes),
    }),
  regenerateSrtSinkPassphrase: () => request('/api/local/srt-sink/regenerate', { method: 'POST' }),
  setSrtSinkLatency: (latencyMs) =>
    request('/api/local/srt-sink/latency', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ latency_ms: latencyMs }),
    }),
  setSrtSinkDebugOverlay: (enabled) =>
    request('/api/local/srt-sink/debug-overlay', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    }),
  revelationEnabled: () => request('/api/local/revelation/enabled'),
  setRevelationEnabled: (enabled) =>
    request('/api/local/revelation/enabled', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    }),
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
}
