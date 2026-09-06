"""Replaces the old system/scripts/srt-sink-monitor.py + mpv/DRM-takeover
video sink: an in-process asyncio task (started from main.py's lifespan,
same pattern as heartbeat.py/sync.py) that watches for an incoming SRT
stream and, instead of stopping the kiosk to play it via mpv, remuxes the
still-encoded H.264 (ffmpeg `-c:v copy` — no decode/re-encode; audio is a
real, cheap encode instead — see _ffmpeg_cmd()'s own comment for why)
into fragmented MP4 and forwards it to the kiosk page itself over a WebSocket
(/api/local/srt-sink/stream), which plays it inline via MediaSource
Extensions. Chromium never stops being the active kiosk process, so none
of display-power.py's `takeover`/sleep-restore dance is needed here
anymore, and this no longer needs to run as a separate root-owned systemd
unit with DRM group access — it's just another asyncio task in this
backend, same as heartbeat/sync.

Being in-process also removes the old script's reason for duplicating
srt_sink.py's read_config()/effective_enabled() logic (that duplication
existed only because the old daemon ran outside this venv, as a
standalone /usr/local/sbin script) — this module imports srt_sink
directly.

Unlike the old daemon, this doesn't poll for a candidate before handing
off to a real listener — it just runs ffmpeg as the SRT listener
continuously, for as long as Settings > SRT Sink is enabled, restarting
it whenever it exits (a finished clip, a dropped connection, the
passphrase changing). An earlier version here used a throwaway raw-UDP
"peek" socket first (the old daemon's poll-then-launch trick, still
described in its own retired docstring), on the theory that avoiding a
continuously-running ffmpeg was worth the extra step, the way it was
worth it for mpv. Confirmed on hardware that this doesn't actually work:
the peek socket isn't SRT-aware, so it can't complete a handshake — it
just silently swallows whichever UDP packet happens to land on it
(typically the sender's induction packet) and hands off to a
*time-limited* real listener a moment later. A real sender, having had
that first packet vanish into something that never replies, falls back
to its own reconnect cycle — which doesn't necessarily land inside our
validation window — so the two kept missing each other: forever
"reconnecting" from the sender's side, forever timing out with zero
output on ours. The whole optimization was unnecessary anyway: it
existed only to avoid running mpv (CPU/DRM-heavy) continuously, but
ffmpeg doing pure remux costs nothing while idly waiting for a caller, so
now it just sits there the whole time, like any normal server socket.

Fragmentation (`-frag_duration` alone — deliberately no `frag_keyframe`,
see _ffmpeg_cmd()'s own comment) is deliberately decoupled from the
source's keyframe/GOP interval, which isn't controllable (varies by
sender — OBS, a screen-share encoder, etc., commonly 1-2s): tying
fragment emission to keyframes would inherit a full GOP of latency.
FRAG_DURATION_US is a threshold, not a hard cut — ffmpeg's muxer flushes
at the first frame crossing it, so real granularity is bounded by the
source's own frame interval regardless of
how low this is set.

A client that connects mid-stream (e.g. the kiosk page reloading) needs
an init segment (ftyp+moov, emitted once via `empty_moov`) to bootstrap
its MediaSource, and enough fragments since the last keyframe for
MSE to actually have something decodable to start from — a fragment
boundary alone doesn't guarantee a keyframe, since most fragments here
are far smaller than a full GOP. Rather than parse moof/traf sample
flags to find true keyframe boundaries, this keeps a small rolling cache
of the last MAX_CACHED_FRAGMENTS fragments (a couple hundred KB at most)
and replays all of it to a joining client — statistically guaranteed to
span at least one keyframe given the cache window is wider than any
realistic GOP length, at the cost of a joining client occasionally
decoding from a few frames before the nearest keyframe (Chromium simply
can't render those and skips them — a handful of dropped/black frames on
join, not a fatal error).
"""
import asyncio
import time
from collections import deque
from urllib.parse import quote

from fastapi import WebSocket, WebSocketDisconnect

import srt_sink

# How often serve_client() reports its own per-client queue depth back to
# the browser (see that function's own comment).
QUEUE_REPORT_INTERVAL_SECONDS = 1.0

# Threshold, not a hard cut — see module docstring. Started at 50ms, but
# confirmed on hardware that landed on a periodic, ~10-15s stall-then-
# jump: audio kept playing smoothly through each stall while video froze
# then snapped forward, the signature of a main/renderer-thread GC pause
# (Chromium runs audio output on its own thread, largely decoupled from
# the main thread video compositing needs) rather than anything decode-
# rate related. At 50ms fragments the client was allocating a new
# ArrayBuffer ~20 times/sec purely from WebSocket messages — real
# allocation pressure on a memory-constrained Pi. 200ms cuts that
# roughly 4x at the cost of a bit more latency, which the client's
# catch-up logic now has headroom for.
FRAG_DURATION_US = 200_000
# How often the manager loop rechecks Settings > SRT Sink's enable
# toggle/passphrase — both while disabled (to notice it turning back on)
# and while a listener is already running (to notice it turning off, or
# the passphrase changing, and restart accordingly). Not a UDP poll
# interval — see module docstring for why there's no raw socket here.
CONFIG_POLL_INTERVAL_SECONDS = 2.0
# ~3s of fragments at FRAG_DURATION_US=200ms — comfortably wider than any
# realistic source GOP (typically 1-2s), so a joining client's replay
# almost always spans at least one keyframe. See module docstring.
MAX_CACHED_FRAGMENTS = 15
# Bounded so one stalled client can't make the broadcast loop back up
# indefinitely; a client that falls this far behind is treated as
# unrecoverable and dropped rather than blocking everyone else.
CLIENT_QUEUE_MAXSIZE = 200


class _StreamState:
    def __init__(self):
        self.active = False
        self.mime_codec: str | None = None
        self.init_segment = b""
        self.recent_fragments: deque[bytes] = deque(maxlen=MAX_CACHED_FRAGMENTS)
        self.clients: set[asyncio.Queue] = set()
        # Incremental MP4 box parser state — see _feed_boxes().
        self._parse_buf = b""
        self._seen_moof = False
        self._current_fragment = bytearray()

    def reset(self):
        self.mime_codec = None
        self.init_segment = b""
        self.recent_fragments.clear()
        self._parse_buf = b""
        self._seen_moof = False
        self._current_fragment = bytearray()


_state = _StreamState()


def is_playing() -> bool:
    return _state.active


def _listener_url(passphrase: str) -> str:
    return (
        f"srt://0.0.0.0:{srt_sink.SRT_PORT}"
        f"?mode=listener&passphrase={quote(passphrase)}&latency={srt_sink.SRT_LATENCY_MICROSECONDS}"
    )


def _mime_codec_from_moov(moov: bytes) -> str | None:
    """Builds the exact codecs string MediaSource.addSourceBuffer() needs
    — it must list every track present in the segments, or appendBuffer()
    rejects them outright. Hardcoding one video profile/audio codec would
    break for any source that doesn't happen to match it, so both are
    read out of the moov's own box contents rather than assumed:

    - video: profile_idc/profile_compatibility/level_idc straight out of
      the avcC box's AVCDecoderConfigurationRecord (bytes 1-3 of its
      payload) build an exact "avc1.PPCCLL". A raw byte-search for the
      'avcC' tag (rather than walking the full moov->trak->mdia->minf->
      stbl->stsd->avc1->avcC box tree) is a pragmatic shortcut: avcC is a
      well-defined leaf box and a spurious collision with its 4-byte tag
      elsewhere in a well-formed moov isn't a practical concern.
    - audio: presence of an 'mp4a' box means an AAC track is present (the
      near-universal choice for this kind of source — OBS/screen-share
      encoders default to it), reported as "mp4a.40.2" (AAC-LC) without
      reading the exact audioObjectType out of the esds box's bit-packed
      DecoderSpecificInfo — a source encoding some other AAC profile
      would need that read done properly; not attempted here.
    """
    idx = moov.find(b"avcC")
    if idx == -1 or idx + 7 >= len(moov):
        return None
    profile, compat, level = moov[idx + 5], moov[idx + 6], moov[idx + 7]
    codecs = [f"avc1.{profile:02x}{compat:02x}{level:02x}"]
    if b"mp4a" in moov:
        codecs.append("mp4a.40.2")
    return f'video/mp4; codecs="{",".join(codecs)}"'


def _feed_boxes(chunk: bytes) -> list[bytes]:
    """Incrementally splits ffmpeg's raw stdout byte stream into top-level
    MP4 boxes, and returns the box-aligned units (the init segment, once;
    each subsequent completed fragment, moof+mdat) newly completed by this
    call, ready to broadcast.

    Live broadcast used to just forward whatever raw, arbitrarily-sized
    slice came back from proc.stdout.read() — fine for a client that's
    been receiving every byte since the start, but confirmed on hardware
    to break a client connecting mid-stream: the very next live chunk
    after its cached-fragment replay could land mid-fragment (a fragment
    only a couple hundred ms long is very likely still in progress when a
    client joins), handing it a byte range with no moof header at its
    start. Chromium's demuxer has no tolerance for that ("stream parsing
    failed"). Routing
    every broadcast through this same box-aligned unit list — the exact
    same units a joining client's cached replay uses — means no client,
    old or new, ever receives a partial box."""
    ready: list[bytes] = []
    _state._parse_buf += chunk
    while True:
        buf = _state._parse_buf
        if len(buf) < 8:
            break
        size = int.from_bytes(buf[0:4], "big")
        box_type = buf[4:8]
        if size in (0, 1) or len(buf) < size:
            # size==0 (extends to EOF) / size==1 (64-bit largesize) don't
            # occur for ftyp/moov/moof/mdat from this muxer in practice;
            # bail rather than mis-parse. Also covers "need more data".
            if size in (0, 1):
                _state._parse_buf = b""
            break
        box = buf[:size]
        _state._parse_buf = buf[size:]
        if not _state._seen_moof:
            if box_type == b"moof":
                _state._seen_moof = True
                _state._current_fragment = bytearray(box)
                # Everything before this first moof — ftyp+moov — is now
                # a complete, self-contained init segment.
                ready.append(bytes(_state.init_segment))
            else:
                _state.init_segment += box
                if box_type == b"moov":
                    _state.mime_codec = _mime_codec_from_moov(_state.init_segment)
        else:
            if box_type == b"moof":
                fragment = bytes(_state._current_fragment)
                _state.recent_fragments.append(fragment)
                ready.append(fragment)
                _state._current_fragment = bytearray(box)
            else:
                _state._current_fragment += box
    return ready


def _end_client(queue: asyncio.Queue) -> None:
    """Sentinel telling serve_client() to close out. Only ever called
    right after removing `queue` from _state.clients, so nothing else is
    still producing into it — safe to drop whatever's still queued (a
    disconnecting/stale client doesn't need it) to guarantee room for the
    sentinel, rather than risk it never arriving because the queue was
    already full."""
    while not queue.empty():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    queue.put_nowait(None)


def _broadcast(chunk: bytes) -> None:
    stale = []
    for queue in _state.clients:
        try:
            queue.put_nowait(chunk)
        except asyncio.QueueFull:
            stale.append(queue)
    for queue in stale:
        _state.clients.discard(queue)
        _end_client(queue)


def _ffmpeg_cmd(passphrase: str) -> list[str]:
    return [
        "ffmpeg",
        "-loglevel", "warning", "-nostats",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-i", _listener_url(passphrase),
        "-c:v", "copy",
        # Video stays a pure stream copy — no decode/re-encode, the whole
        # point of this design. Audio is a real encode, not a copy: confirmed
        # on hardware that reframing OBS's ADTS AAC into fmp4's "raw" framing
        # via -bsf:a aac_adtstoasc (a pure bitstream reframe, in theory) was
        # producing an audio track Chromium's MSE ChunkDemuxer rejected
        # outright ("stream parsing failed") even though the resulting esds
        # bytes looked structurally standard by hand — video-only (-an)
        # confirmed audio was the actual cause. Re-encoding sidesteps
        # whatever's subtly wrong with that bitstream filter's output for
        # this specific source; audio encode is cheap (a small fraction of
        # what video decode would cost), so this doesn't meaningfully
        # compromise the "no decode/re-encode" goal that actually matters
        # (the video path).
        "-c:a", "aac",
        # Drops OBS's own injected udta/meta metadata box.
        "-map_metadata", "-1",
        # Deliberately no `frag_keyframe`: it forces an *extra* fragment
        # cut at every keyframe, independent of and in addition to
        # -frag_duration below — confirmed on hardware that when a
        # keyframe landed shortly after a duration-triggered cut, the
        # resulting fragment was short enough to contain zero audio
        # frames (~21ms each), which Chromium's ChunkDemuxer flagged
        # constantly ("Media segment did not contain any coded frames
        # for track 1"). Fragments don't need to start on a keyframe for
        # this use case — continuous linear playback, no seeking, no
        # independent fragment access — so there's no reason to pay for
        # the extra irregular cuts. -frag_duration alone still fragments
        # just fine without it.
        "-movflags", "empty_moov+default_base_moof",
        "-frag_duration", str(FRAG_DURATION_US),
        "-f", "mp4", "pipe:1",
    ]


async def _drain_stderr(proc, tail: deque) -> None:
    """ffmpeg writes its own logging to stderr continuously (SRT connect/
    handshake messages included) — has to be actively drained or ffmpeg
    blocks the moment that pipe's ~64KB kernel buffer fills, which would
    otherwise silently wedge the whole stream. Each line is printed live
    (shows up in `journalctl -u slide-announcer-backend`) and kept in
    `tail` so an unexpected exit can log the last few lines that likely
    explain it."""
    try:
        async for raw_line in proc.stderr:
            line = raw_line.decode(errors="replace").rstrip()
            tail.append(line)
            print(f"[srt-stream-bridge] ffmpeg: {line}", flush=True)
    except (asyncio.CancelledError, ValueError):
        pass


async def _pump_stdout(proc, stderr_tail: deque) -> None:
    """Reads ffmpeg's stdout until it exits (a real caller disconnecting,
    or an error), feeding the box parser/broadcasting to clients and
    flipping _state.active the moment real output starts. No validation
    timeout — an idle listener with no caller yet just waits, same as any
    normal server socket; see module docstring for why an earlier version
    tried to time-box this and why that didn't work."""
    validated = False
    try:
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            if not validated:
                validated = True
                _state.active = True
                print("[srt-stream-bridge] validated — stream is live", flush=True)
            for unit in _feed_boxes(chunk):
                _broadcast(unit)
    finally:
        _state.active = False
        for queue in list(_state.clients):
            _end_client(queue)
        print(
            f"[srt-stream-bridge] ffmpeg exited code={proc.returncode}. "
            f"Last stderr: {list(stderr_tail)}",
            flush=True,
        )


async def run_forever():
    """Keeps exactly one ffmpeg SRT listener running for as long as
    Settings > SRT Sink is enabled, (re)launching it whenever it isn't
    running yet, and restarting it whenever it exits on its own or the
    enable toggle/passphrase changes underneath it."""
    proc = None
    pump_task = None
    stderr_task = None
    active_passphrase = None

    async def _teardown():
        nonlocal proc, pump_task, stderr_task, active_passphrase
        if proc is not None and proc.returncode is None:
            proc.kill()
        if pump_task is not None:
            await pump_task
        if stderr_task is not None:
            stderr_task.cancel()
        proc = pump_task = stderr_task = None
        active_passphrase = None

    was_enabled = None
    try:
        while True:
            config = srt_sink.read_config()
            enabled = srt_sink.effective_enabled(config)
            if enabled != was_enabled:
                print(f"[srt-stream-bridge] effective_enabled={enabled}", flush=True)
                was_enabled = enabled
            passphrase = config["passphrase"] if enabled else None

            if proc is not None and (not enabled or passphrase != active_passphrase):
                print("[srt-stream-bridge] config changed, restarting listener", flush=True)
                await _teardown()

            if not enabled:
                await asyncio.sleep(CONFIG_POLL_INTERVAL_SECONDS)
                continue

            if proc is None:
                _state.reset()
                cmd = _ffmpeg_cmd(passphrase)
                print(f"[srt-stream-bridge] starting listener: {' '.join(cmd)}", flush=True)
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                stderr_tail: deque = deque(maxlen=20)
                stderr_task = asyncio.create_task(_drain_stderr(proc, stderr_tail))
                pump_task = asyncio.create_task(_pump_stdout(proc, stderr_tail))
                active_passphrase = passphrase

            done, _ = await asyncio.wait({pump_task}, timeout=CONFIG_POLL_INTERVAL_SECONDS)
            if pump_task in done:
                # ffmpeg exited on its own (clip ended, dropped
                # connection, a real error) — clean up and loop straight
                # back into relaunching it.
                await _teardown()
    finally:
        await _teardown()


async def serve_client(websocket: WebSocket) -> None:
    """Registers `websocket` (already accepted by the caller) as a live
    viewer: replays the cached init segment + recent fragments so it can
    bootstrap immediately, then streams live fragments until the source
    disconnects or the client goes away."""
    if not _state.active:
        await websocket.close(code=1000)
        return

    # Snapshot the replay data and register for live broadcast in the
    # same synchronous step — no `await` anywhere in this block, so
    # _pump_stdout() (a separate task) cannot run in between and
    # broadcast a live chunk into `queue` while we're still separately
    # about to replay the cached fragments below. Without this, a client
    # could receive the same bytes twice (once via the cached replay,
    # once via a live broadcast that snuck in mid-replay), corrupting the
    # MP4 box structure it sees. Confirmed on hardware: this was the
    # actual cause of Chromium's "stream parsing failed" append error.
    queue: asyncio.Queue = asyncio.Queue(maxsize=CLIENT_QUEUE_MAXSIZE)
    mime_codec = _state.mime_codec
    init_segment = _state.init_segment
    cached_fragments = list(_state.recent_fragments)
    _state.clients.add(queue)

    try:
        if mime_codec:
            await websocket.send_json({"mimeCodec": mime_codec})
        await websocket.send_bytes(init_segment)
        for fragment in cached_fragments:
            await websocket.send_bytes(fragment)

        # Periodically reports this client's own queue depth back over
        # the same connection — added to check a real blind spot: this
        # queue sits between ffmpeg's output and the browser, and
        # `websocket.send_bytes()` applies real backpressure if the
        # browser is ever slow to read, regardless of whether either
        # side's CPU shows it. The client-side append queue alone can't
        # reveal that, since it only sees what's already arrived. Folded
        # into this same single send loop (not a separate task) since
        # concurrent sends on one WebSocket aren't safe to interleave.
        last_report = time.monotonic()
        while True:
            remaining = QUEUE_REPORT_INTERVAL_SECONDS - (time.monotonic() - last_report)
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=max(remaining, 0.001))
            except asyncio.TimeoutError:
                await websocket.send_json({"queueDepth": queue.qsize()})
                last_report = time.monotonic()
                continue
            if chunk is None:
                break
            await websocket.send_bytes(chunk)
            if time.monotonic() - last_report >= QUEUE_REPORT_INTERVAL_SECONDS:
                await websocket.send_json({"queueDepth": queue.qsize()})
                last_report = time.monotonic()
    except WebSocketDisconnect:
        pass
    finally:
        _state.clients.discard(queue)
