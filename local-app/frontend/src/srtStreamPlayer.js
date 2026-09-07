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

// Needs real headroom relative to srt_stream_bridge.py's FRAG_DURATION_US
// (200ms) — confirmed on hardware that 0.3s here, barely 1.5 fragment-
// intervals, left so little margin that ordinary arrival jitter (or the
// catch-up nudge below actively pushing currentTime toward the live
// edge) could outrun the buffered data entirely. That's a genuine buffer
// underrun, not a rendering stall — it stalls audio too, since there's
// really no more data to decode for either track, unlike a GC pause
// (which only stalls video compositing on the main thread while audio's
// own thread keeps playing already-buffered audio through it).
//
// Lowered from 0.6 to 0.4 while chasing overall end-to-end latency —
// still a full 2 fragment-intervals of margin at 200ms, comfortably
// above the already-proven-bad 0.3s above. If FRAG_DURATION_US ever
// drops (a separate, not-yet-tried latency lever), this needs revisiting
// too: the safety margin here is relative to fragment duration, not an
// absolute constant.
// Diagnostic flag: when true, runCatchup() never touches playbackRate or
// currentTime (see its own guard) — only trimBuffer() still runs. Used
// to confirm on hardware whether the catch-up logic itself (the rate
// nudge or the hard seek) was driving the buffered range to empty, or
// whether that happened independent of it. Result: disabling it stopped
// the buffer from emptying, but stalls persisted (and got worse) even
// without any seeking/rate changes — so catch-up was reacting to (and
// amplifying the visible symptom of) a real decode/render bottleneck
// elsewhere, not causing the underlying stall itself. Left here, default
// off, in case it's useful again while chasing that bottleneck.
const NO_CATCHUP = false
const TARGET_LATENCY_SECONDS = 0.4
const SMALL_DRIFT_SECONDS = 0.05
const LARGE_DRIFT_SECONDS = 1.3
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
//
// A diagnostic test with this pushed sky-high (3600) confirmed
// trimBuffer()'s sourceBuffer.remove() call as the actual cause of the
// ~15s freezes: debug logging showed the buffered range going fully
// empty in the same tick removeEnd first passed the buffer's start — our
// fragments aren't keyframe-aligned (see _ffmpeg_cmd()'s deliberate
// no-frag_keyframe choice), so Chromium's remove() had no clean
// random-access point to split on and evicted the whole range instead
// of the requested sliver. Fixed properly now: trimBuffer() clamps its
// removal end to lastConfirmedKeyframeTime (see its own comment and
// srt_stream_bridge.py's _parse_fragment_keyframe()), a real
// random-access point remove() can always split on cleanly — safe to
// restore this to its original value.
const BUFFER_TRIM_KEEP_SECONDS = 5
let ws = null
let mediaSource = null
let sourceBuffer = null
let appendQueue = []
let catchupTimer = null
let videoEl = null
let overlayEl = null
// On-screen metrics HUD, updated every catch-up tick — added while
// actively tuning latency/stall behavior on real hardware, since reading
// numbers directly off the TV beats round-tripping through devtools/
// journalctl for every adjustment. Controlled by Settings > Video
// Receiver's "Debug overlay" toggle (srt_sink.py's debug_overlay field) —
// see setDebugOverlay(), called from Slideshow.vue's existing
// srt-sink/playing poll, so flipping it takes effect within a second
// without needing to restart the stream.
let debugOverlayEnabled = false
// Reported periodically by srt_stream_bridge.py's serve_client() — the
// depth of ffmpeg-output-to-WebSocket-send queue on the SERVER side,
// which the client-side appendQueue can't see: it only reflects data
// that's already arrived. A backlog here means the browser was slow to
// read from the socket (for any reason), independent of appendQueue.
let serverQueueDepth = null
// The most recent confirmed video keyframe timestamp (seconds, same
// timeline as videoEl.currentTime/buffered — this file never sets
// sourceBuffer.timestampOffset, so no correction is needed), reported
// alongside serverQueueDepth by srt_stream_bridge.py's serve_client().
// trimBuffer() never removes past this point — see its own comment.
let lastConfirmedKeyframeTime = null
// Bumped each reconnect attempt after the server closes with
// LATE_JOIN_TIMEOUT_CLOSE_CODE (see onSourceOpen's ws.onclose) — capped
// so a stream stuck with no keyframes at all doesn't retry forever.
let lateJoinRetryCount = 0
const LATE_JOIN_TIMEOUT_CLOSE_CODE = 4000
const LATE_JOIN_MAX_RETRIES = 3
const LATE_JOIN_RETRY_DELAY_MS = 1000

// Rolling history of fragment-arrival/append/buffered-range events, only
// kept while the debug overlay is on (Settings > SRT Sink's toggle — see
// setDebugOverlay()). A kiosk has no devtools timeline to scrub back
// through after a freeze, so this is that timeline: bounded to the last
// DEBUG_LOG_MAX_EVENTS so normal playback doesn't grow it unbounded, and
// flushed to the server log (via logClient(), landing alongside
// srt_stream_bridge.py's own per-fragment debug logging in the same
// journalctl stream) the moment runCatchup() notices the buffered range
// has actually gone empty — the buf=[0.00,0.00]/negative-gap symptom —
// rather than on every tick, so the dump captures exactly the seconds
// leading into a stall instead of spamming one every 250ms.
const DEBUG_LOG_MAX_EVENTS = 80
let debugEvents = []
let lastBufferedEnd = 0
let stallFlushed = false

function recordEvent(type, detail) {
  if (!debugOverlayEnabled) return
  debugEvents.push({ atIso: new Date().toISOString(), type, detail })
  if (debugEvents.length > DEBUG_LOG_MAX_EVENTS) debugEvents.shift()
}

function flushDebugEvents(reason) {
  const dump = debugEvents.map((e) => `${e.atIso} ${e.type} ${JSON.stringify(e.detail)}`).join(' | ')
  logClient(`debug dump (${reason}): ${dump}`)
}

function rangesToString(ranges) {
  const parts = []
  for (let i = 0; i < ranges.length; i++) parts.push(`${ranges.start(i).toFixed(2)}-${ranges.end(i).toFixed(2)}`)
  return parts.join(',') || 'empty'
}

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
  debugEvents = []
  lastBufferedEnd = 0
  stallFlushed = false
  lateJoinRetryCount = 0
  videoEl = el
  videoEl.onerror = () => {
    const err = videoEl.error
    logClient(`<video> error code=${err?.code} message=${err?.message}`)
  }
  mediaSource = new MediaSource()
  mediaSource.addEventListener('error', () => logClient('MediaSource error event'))
  videoEl.src = URL.createObjectURL(mediaSource)
  mediaSource.addEventListener('sourceopen', onSourceOpen, { once: true })
  if (debugOverlayEnabled) {
    ensureOverlay()
    startDebugHeartbeat()
  }
}

// Called from Slideshow.vue on every srt-sink/playing poll — cheap and
// idempotent, so no need to track whether the setting actually changed.
export function setDebugOverlay(enabled) {
  debugOverlayEnabled = enabled
  if (!videoEl) return
  if (enabled) {
    ensureOverlay()
    startDebugHeartbeat()
  } else {
    if (overlayEl) {
      overlayEl.remove()
      overlayEl = null
    }
    stopDebugHeartbeat()
  }
}

// Periodic full client-state dump, independent of whether playback ever
// actually starts — added after a late-join hang produced ZERO further
// log output past "SourceBuffer created" (no error, no timeout, nothing)
// despite a real keyframe fragment being broadcast minutes earlier.
// Root cause: every other piece of client-side diagnostics
// (recordEvent()/flushDebugEvents()) only ever gets flushed from inside
// runCatchup()'s stall-check, and runCatchup() only starts once
// maybeWarmUp() successfully calls videoEl.play() — which itself
// requires readyState>=2. If playback never starts, that whole
// diagnostic path never activates, so a *failure to start* was
// completely invisible. This heartbeat starts as soon as the WebSocket
// connection attempt begins (not gated on anything succeeding) so a
// stuck join is always visible in the logs somewhere.
let debugHeartbeatTimer = null
const DEBUG_HEARTBEAT_INTERVAL_MS = 3000

function startDebugHeartbeat() {
  if (debugHeartbeatTimer || !debugOverlayEnabled) return
  debugHeartbeatTimer = setInterval(() => {
    const err = videoEl?.error
    logClient(
      `heartbeat ws=${ws?.readyState} appendQueueLen=${appendQueue.length} ` +
      `sourceBuffer=${sourceBuffer ? `updating=${sourceBuffer.updating} buffered=${rangesToString(sourceBuffer.buffered)}` : 'none'} ` +
      `video paused=${videoEl?.paused} readyState=${videoEl?.readyState} currentTime=${videoEl?.currentTime?.toFixed(2)} ` +
      `error=${err ? `${err.code}:${err.message}` : 'none'} mediaSourceReadyState=${mediaSource?.readyState}`
    )
  }, DEBUG_HEARTBEAT_INTERVAL_MS)
}

function stopDebugHeartbeat() {
  if (debugHeartbeatTimer) clearInterval(debugHeartbeatTimer)
  debugHeartbeatTimer = null
}

export function stopSrtStream() {
  if (catchupTimer) clearInterval(catchupTimer)
  catchupTimer = null
  stopDebugHeartbeat()
  if (ws) ws.close()
  ws = null
  sourceBuffer = null
  mediaSource = null
  appendQueue = []
  serverQueueDepth = null
  lastConfirmedKeyframeTime = null
  if (videoEl) {
    videoEl.onerror = null
    videoEl.pause()
    videoEl.removeAttribute('src')
    videoEl.load()
  }
  videoEl = null
  if (overlayEl) {
    overlayEl.remove()
    overlayEl = null
  }
}

function ensureOverlay() {
  if (overlayEl) return
  overlayEl = document.createElement('div')
  overlayEl.style.cssText = `
    position: fixed; top: 8px; left: 8px; z-index: 2147483647;
    background: rgba(0, 0, 0, 0.7); color: #0f0; font: 12px/1.4 monospace;
    padding: 6px 10px; white-space: pre; pointer-events: none;
  `
  document.body.appendChild(overlayEl)
}

function updateOverlay(buffered) {
  if (!overlayEl || !videoEl) return
  const end = buffered.length ? buffered.end(buffered.length - 1) : 0
  const start = buffered.length ? buffered.start(0) : 0
  const quality = videoEl.getVideoPlaybackQuality?.()
  overlayEl.textContent =
    `ct=${videoEl.currentTime.toFixed(2)} buf=[${start.toFixed(2)},${end.toFixed(2)}] ` +
    `gap=${(end - videoEl.currentTime).toFixed(2)} rate=${videoEl.playbackRate.toFixed(2)}\n` +
    `queued=${appendQueue.length} serverQueued=${serverQueueDepth ?? '?'} ` +
    `readyState=${videoEl.readyState} ws=${ws?.readyState}\n` +
    (quality
      ? `frames total=${quality.totalVideoFrames} dropped=${quality.droppedVideoFrames} ` +
        `corrupted=${quality.corruptedVideoFrames}`
      : 'frames: unavailable')
}

function onSourceOpen() {
  logClient('MediaSource sourceopen — connecting WebSocket')
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  ws = new WebSocket(`${proto}//${location.host}/api/local/srt-sink/stream`)
  ws.binaryType = 'arraybuffer'
  ws.onopen = () => logClient('WebSocket connected')
  ws.onerror = (event) => logClient(`WebSocket error: ${event.message || event}`)
  ws.onclose = (event) => {
    logClient(`WebSocket closed code=${event.code} reason=${event.reason}`)
    // The server gave up waiting for a real keyframe to start this join
    // from (see srt_stream_bridge.py's LATE_JOIN_KEYFRAME_TIMEOUT_SECONDS)
    // rather than leaving the connection hanging forever — retry a few
    // times with a short delay (a keyframe should show up within the
    // next GOP or two) before giving up and just logging, consistent
    // with this file's existing pattern of no visible kiosk error UI.
    // Reuses the existing mediaSource/sourceBuffer rather than tearing
    // the whole pipeline down — see initSourceBuffer()'s own reuse guard.
    if (event.code === LATE_JOIN_TIMEOUT_CLOSE_CODE && lateJoinRetryCount < LATE_JOIN_MAX_RETRIES) {
      lateJoinRetryCount += 1
      logClient(`retrying after no-keyframe timeout (attempt ${lateJoinRetryCount}/${LATE_JOIN_MAX_RETRIES})`)
      setTimeout(() => {
        if (mediaSource) onSourceOpen()
      }, LATE_JOIN_RETRY_DELAY_MS)
    } else if (event.code === LATE_JOIN_TIMEOUT_CLOSE_CODE) {
      logClient(`giving up after ${LATE_JOIN_MAX_RETRIES} no-keyframe retries`)
    }
  }
  ws.onmessage = (event) => {
    if (typeof event.data === 'string') {
      // Text frames — the one-time mimeCodec at connect, and a periodic
      // queueDepth report after that. Everything else is binary MP4
      // (init segment, then live fragments) — see srt_stream_bridge.py's
      // serve_client().
      const msg = JSON.parse(event.data)
      if (msg.mimeCodec) initSourceBuffer(msg.mimeCodec)
      if (typeof msg.queueDepth === 'number') {
        serverQueueDepth = msg.queueDepth
        recordEvent('server-queue-depth', { depth: msg.queueDepth })
      }
      if (typeof msg.keyframeTime === 'number') lastConfirmedKeyframeTime = msg.keyframeTime
      return
    }
    recordEvent('ws-fragment-arrived', { bytes: event.data.byteLength, queuedAhead: appendQueue.length })
    appendQueue.push(event.data)
    flushAppendQueue()
  }
  // A server-side close (source disconnected) is otherwise silent here —
  // Slideshow.vue's own EXTERNAL_PLAYBACK_POLL_MS poll of
  // /api/local/srt-sink/playing is what actually notices and calls
  // stopSrtStream(), same as it noticed the mpv sink stopping before.
}

function initSourceBuffer(mimeCodec) {
  if (sourceBuffer) {
    // A no-keyframe-timeout reconnect (see onSourceOpen's ws.onclose)
    // resends mimeCodec/the init segment on the new connection — same
    // SourceBuffer as before, so just flush the resent init segment
    // into it rather than calling addSourceBuffer() again (which would
    // throw or create an unwanted duplicate track).
    flushAppendQueue()
    return
  }
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
  sourceBuffer.addEventListener('updateend', () =>
    recordEvent('append-updateend', { buffered: rangesToString(videoEl.buffered) }))
  sourceBuffer.addEventListener('error', () => logClient('SourceBuffer error event'))
  logClient('SourceBuffer created — appending queued data')
  flushAppendQueue()
}

function flushAppendQueue() {
  if (!sourceBuffer || sourceBuffer.updating || appendQueue.length === 0) return
  // Steady state this is almost always exactly one fragment — but if the
  // client ever falls behind (a GC pause, a slow tick), several can back
  // up here. Merging them into one appendBuffer() call instead of one
  // per fragment means fewer objects/calls right when things are
  // already under memory pressure, the moment that matters most.
  const buf = appendQueue.length === 1 ? appendQueue.shift() : mergeQueued()
  recordEvent('append-start', { bytes: buf.byteLength, bufferedBefore: rangesToString(videoEl.buffered) })
  try {
    sourceBuffer.appendBuffer(buf)
  } catch (err) {
    recordEvent('append-threw', { error: String(err) })
    logClient(`appendBuffer threw: ${err}`)
    return
  }
  maybeWarmUp()
}

function mergeQueued() {
  let total = 0
  for (const buf of appendQueue) total += buf.byteLength
  const merged = new Uint8Array(total)
  let offset = 0
  for (const buf of appendQueue) {
    merged.set(new Uint8Array(buf), offset)
    offset += buf.byteLength
  }
  appendQueue.length = 0
  return merged
}

function bufferedEnd() {
  return videoEl.buffered.length ? videoEl.buffered.end(videoEl.buffered.length - 1) : null
}

function maybeWarmUp() {
  // Deliberately does NOT also check videoEl.readyState here (it used
  // to, `|| videoEl.readyState < 2`) — that was a real deadlock for any
  // late join. readyState reflects whether there's buffered data AT THE
  // CURRENT PLAYBACK POSITION, and currentTime defaults to 0; for a
  // fresh stream start the first buffered fragment also starts near 0,
  // so they coincide and readyState naturally reaches 2 — but for a
  // late join the buffered range starts wherever the live stream
  // actually is (tens of seconds in), nowhere near currentTime=0, so
  // readyState could never reach 2 on its own. The only code that
  // repositions currentTime into the buffered range is right here,
  // below — gating entry to this function on readyState>=2 meant it
  // could never run for exactly the case it needs to fix. Confirmed on
  // hardware: a late join's SourceBuffer grew for 40+ seconds
  // (appendBuffer succeeding the whole time) while currentTime/readyState
  // sat frozen at 0/1 forever. `!videoEl.paused` alone is sufficient to
  // still only fire once — per spec, .paused flips to false
  // synchronously the moment .play() is called below, before its
  // promise even resolves.
  if (!videoEl.paused) return
  const end = bufferedEnd()
  if (end === null) return
  videoEl.currentTime = Math.max(0, end - TARGET_LATENCY_SECONDS)
  videoEl.play()
    .then(() => logClient(`playback started at currentTime=${videoEl.currentTime.toFixed(2)}`))
    .catch((err) => logClient(`video.play() rejected: ${err}`))
  if (!catchupTimer) catchupTimer = setInterval(runCatchup, CATCHUP_INTERVAL_MS)
}

function runCatchup() {
  // TimeRanges accessors commonly hand back a fresh object per read in
  // Chromium — read `.buffered` exactly once per tick and share it with
  // trimBuffer() below, instead of each independently re-reading it.
  const buffered = videoEl.buffered
  if (overlayEl) updateOverlay(buffered)
  const currentEnd = buffered.length ? buffered.end(buffered.length - 1) : 0
  const gap = currentEnd - videoEl.currentTime
  recordEvent('catchup-tick', {
    currentTime: videoEl.currentTime,
    buffered: rangesToString(buffered),
    gap,
    playbackRate: videoEl.playbackRate,
  })
  // The buf=[0.00,0.00]/negative-gap symptom: the buffered range has
  // gone fully empty (or ends behind currentTime) even though fragments
  // are still arriving over the WebSocket — i.e. something is removing
  // data that was already appended, rather than data simply not arriving
  // in time. Dump the event history once per stall (not every tick while
  // it persists) so the flush lands right as it starts, and re-arm once
  // the gap recovers so the next stall gets its own dump.
  if (debugOverlayEnabled) {
    if (gap < 0 && !stallFlushed) {
      stallFlushed = true
      recordEvent('stall-detected', { previousBufferedEnd: lastBufferedEnd, currentTime: videoEl.currentTime })
      flushDebugEvents('buffered range emptied/behind currentTime')
    } else if (gap >= 0 && stallFlushed) {
      stallFlushed = false
    }
    lastBufferedEnd = currentEnd
  }
  if (!buffered.length) return
  if (NO_CATCHUP) {
    videoEl.playbackRate = 1
    trimBuffer(buffered)
    return
  }
  const end = currentEnd
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
  trimBuffer(buffered)
}

function trimBuffer(buffered) {
  if (!sourceBuffer || sourceBuffer.updating || !buffered.length) return
  const start = buffered.start(0)
  let removeEnd = videoEl.currentTime - BUFFER_TRIM_KEEP_SECONDS
  // Never remove past the last confirmed keyframe — sourceBuffer.remove()
  // needs a clean random-access point within/at the edge of its range to
  // split on, and our fragments aren't keyframe-aligned (see
  // _ffmpeg_cmd()'s own comment), so an arbitrary time cutoff isn't
  // guaranteed to land on one. See BUFFER_TRIM_KEEP_SECONDS's own
  // comment for the freeze this fixes. If no keyframe has been reported
  // yet (early in a connection), fall back to time-only trimming — a
  // short-lived startup condition, not steady state.
  if (lastConfirmedKeyframeTime !== null) removeEnd = Math.min(removeEnd, lastConfirmedKeyframeTime)
  if (removeEnd > start) {
    try {
      sourceBuffer.remove(start, removeEnd)
    } catch {
      // A remove() while the source is in a state that disallows it
      // (e.g. mid-teardown) just skips this cycle's trim.
    }
  }
}
