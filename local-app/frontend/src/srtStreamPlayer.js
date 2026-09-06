// Plays the live feed local-app/backend/srt_stream_bridge.py forwards over
// /api/local/srt-sink/stream (fragmented MP4, remuxed from SRT with no
// decode/re-encode step) inline via MediaSource Extensions — replacing the
// old mpv/DRM-takeover video sink, which rendered outside this page
// entirely. Slideshow.vue calls startSrtStream()/stopSrtStream() as
// externalPlaybackActive flips, same activation signal as before.
//
// A plain <video>+MediaSource pipeline was built for on-demand playback,
// not live: left to its own defaults it buffers well behind the live edge
// before starting, and has no way to claw back a gap that opens up later
// (a decode hiccup, a GC pause) — unlike WebRTC's purpose-built jitter
// buffer, nothing here self-corrects. So this does that by hand, the same
// way dash.js/hls.js's low-latency modes do:
// - warm-up: start playback near the buffered edge minus TARGET_LATENCY_
//   SECONDS as soon as anything is decodable, rather than waiting for the
//   browser's own "buffered enough for smooth VOD playback" heuristic.
// - catch-up: on CATCHUP_INTERVAL_MS, compare how far currentTime has
//   fallen behind the buffered edge. A small gap gets a playbackRate
//   nudge back toward the target — scaled by how far behind we are (see
//   runCatchup()), so it actually closes real-world drift instead of
//   merely slowing its growth — a large gap (a real stall) gets a hard
//   seek instead, since a visible skip beats a slow crawl back.
// - trims old buffered ranges on the same interval so a multi-hour kiosk
//   session doesn't grow SourceBuffer memory unbounded.
//
// This is a kiosk with no one watching devtools, so lifecycle milestones
// and every failure path also POST to /api/local/srt-sink/client-log — a
// one-line, fire-and-forget relay (see logClient()) that lands in the
// same `journalctl -u slide-announcer-backend` stream as the server-side
// bridge's own logging, since that's the only console anyone's actually
// going to be tailing when something goes wrong here. Deliberately not
// called per-fragment (every ~50ms) — only at connect/error/state-change
// points — so steady-state playback doesn't spam the backend with an
// HTTP request every fragment.

const TARGET_LATENCY_SECONDS = 0.3
const SMALL_DRIFT_SECONDS = 0.05
const LARGE_DRIFT_SECONDS = 1.0
// runCatchup() scales the playbackRate nudge linearly between these two
// as the gap grows from "just over target" to "about to hit
// LARGE_DRIFT_SECONDS" — a flat, barely-visible rate (the original
// approach) recovers only a few ms per interval, far slower than typical
// real-world drift accumulates, so the gap just grew until it kept
// hitting the hard-seek threshold. Scaling means a gap that's actually
// closing in on a seek gets pulled back hard enough to usually avoid one.
const CATCHUP_RATE_MIN = 1.02
const CATCHUP_RATE_MAX = 1.2
const CATCHUP_INTERVAL_MS = 250
// How much trailing buffer to keep behind currentTime when trimming —
// comfortably more than TARGET_LATENCY_SECONDS/LARGE_DRIFT_SECONDS ever
// need, so trimming never competes with the catch-up logic above.
const BUFFER_TRIM_KEEP_SECONDS = 5

let ws = null
let mediaSource = null
let sourceBuffer = null
let appendQueue = []
let catchupTimer = null
let videoEl = null

function logClient(message) {
  console.log(`[srt-stream-player] ${message}`)
  fetch('/api/local/srt-sink/client-log', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  }).catch(() => {})
}

export function startSrtStream(el) {
  stopSrtStream()
  videoEl = el
  videoEl.onerror = () => {
    const err = videoEl.error
    logClient(`<video> error code=${err?.code} message=${err?.message}`)
  }
  mediaSource = new MediaSource()
  mediaSource.addEventListener('error', () => logClient('MediaSource error event'))
  videoEl.src = URL.createObjectURL(mediaSource)
  mediaSource.addEventListener('sourceopen', onSourceOpen, { once: true })
}

export function stopSrtStream() {
  if (catchupTimer) clearInterval(catchupTimer)
  catchupTimer = null
  if (ws) ws.close()
  ws = null
  sourceBuffer = null
  mediaSource = null
  appendQueue = []
  if (videoEl) {
    videoEl.onerror = null
    videoEl.pause()
    videoEl.removeAttribute('src')
    videoEl.load()
  }
  videoEl = null
}

function onSourceOpen() {
  logClient('MediaSource sourceopen — connecting WebSocket')
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  ws = new WebSocket(`${proto}//${location.host}/api/local/srt-sink/stream`)
  ws.binaryType = 'arraybuffer'
  ws.onopen = () => logClient('WebSocket connected')
  ws.onerror = (event) => logClient(`WebSocket error: ${event.message || event}`)
  ws.onclose = (event) => logClient(`WebSocket closed code=${event.code} reason=${event.reason}`)
  ws.onmessage = (event) => {
    if (typeof event.data === 'string') {
      // The one text frame, sent once at connect — see
      // srt_stream_bridge.py's serve_client(). Everything after is binary
      // MP4 (init segment, then live fragments).
      const { mimeCodec } = JSON.parse(event.data)
      if (mimeCodec) initSourceBuffer(mimeCodec)
      return
    }
    appendQueue.push(event.data)
    flushAppendQueue()
  }
  // A server-side close (source disconnected) is otherwise silent here —
  // Slideshow.vue's own EXTERNAL_PLAYBACK_POLL_MS poll of
  // /api/local/srt-sink/playing is what actually notices and calls
  // stopSrtStream(), same as it noticed the mpv sink stopping before.
}

function initSourceBuffer(mimeCodec) {
  const supported = typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(mimeCodec)
  logClient(`mimeCodec="${mimeCodec}" isTypeSupported=${supported}`)
  if (!mediaSource || !supported) return
  try {
    sourceBuffer = mediaSource.addSourceBuffer(mimeCodec)
  } catch (err) {
    logClient(`addSourceBuffer threw: ${err}`)
    return
  }
  sourceBuffer.addEventListener('updateend', flushAppendQueue)
  sourceBuffer.addEventListener('error', () => logClient('SourceBuffer error event'))
  logClient('SourceBuffer created — appending queued data')
  flushAppendQueue()
}

function flushAppendQueue() {
  if (!sourceBuffer || sourceBuffer.updating || appendQueue.length === 0) return
  const buf = appendQueue.shift()
  try {
    sourceBuffer.appendBuffer(buf)
  } catch (err) {
    logClient(`appendBuffer threw: ${err}`)
    return
  }
  maybeWarmUp()
}

function bufferedEnd() {
  return videoEl.buffered.length ? videoEl.buffered.end(videoEl.buffered.length - 1) : null
}

function maybeWarmUp() {
  if (!videoEl.paused || videoEl.readyState < 2) return
  const end = bufferedEnd()
  if (end === null) return
  videoEl.currentTime = Math.max(0, end - TARGET_LATENCY_SECONDS)
  videoEl.play()
    .then(() => logClient(`playback started at currentTime=${videoEl.currentTime.toFixed(2)}`))
    .catch((err) => logClient(`video.play() rejected: ${err}`))
  if (!catchupTimer) catchupTimer = setInterval(runCatchup, CATCHUP_INTERVAL_MS)
}

function runCatchup() {
  const end = bufferedEnd()
  if (end === null) return
  const gap = end - videoEl.currentTime
  if (gap > LARGE_DRIFT_SECONDS) {
    videoEl.currentTime = Math.max(0, end - TARGET_LATENCY_SECONDS)
    videoEl.playbackRate = 1
  } else if (gap > TARGET_LATENCY_SECONDS + SMALL_DRIFT_SECONDS) {
    // Linearly scale the nudge with how close the gap is to the
    // hard-seek threshold — see CATCHUP_RATE_MIN/MAX's own comment.
    const overshoot = gap - TARGET_LATENCY_SECONDS
    const span = LARGE_DRIFT_SECONDS - TARGET_LATENCY_SECONDS
    const proportion = Math.min(overshoot / span, 1)
    videoEl.playbackRate = CATCHUP_RATE_MIN + proportion * (CATCHUP_RATE_MAX - CATCHUP_RATE_MIN)
  } else {
    videoEl.playbackRate = 1
  }
  trimBuffer()
}

function trimBuffer() {
  if (!sourceBuffer || sourceBuffer.updating || !videoEl.buffered.length) return
  const start = videoEl.buffered.start(0)
  const removeEnd = videoEl.currentTime - BUFFER_TRIM_KEEP_SECONDS
  if (removeEnd > start) {
    try {
      sourceBuffer.remove(start, removeEnd)
    } catch {
      // A remove() while the source is in a state that disallows it
      // (e.g. mid-teardown) just skips this cycle's trim.
    }
  }
}
