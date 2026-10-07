"""Replaces the old system/scripts/srt-sink-monitor.py + mpv/DRM-takeover
video sink: an in-process asyncio task (started from main.py's lifespan,
same pattern as heartbeat.py/sync.py) that watches for an incoming SRT
stream and, instead of stopping the kiosk to play it via mpv, remuxes the
still-encoded video (ffmpeg `-c:v copy` — no decode/re-encode, so this is
codec-agnostic: H.264 is the only codec actually seen on hardware so far,
but _mime_codec_from_moov() and friends build a correct codecs= string
for H.265/AV1 too; audio is a real, cheap encode instead — see
_ffmpeg_cmd()'s own comment for why) into fragmented MP4 and forwards it
to the kiosk page itself over a WebSocket
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
passphrase changing). A throwaway raw-UDP "peek" socket approach (the old
daemon's poll-then-launch trick) doesn't work here: it isn't SRT-aware,
so it can't complete a handshake, and swallowing the sender's induction
packet just makes the sender fall back to its own reconnect cycle instead
— the two end up perpetually missing each other. Running ffmpeg
continuously avoids all of that; unlike mpv it costs nothing while idly
waiting for a caller, so there's no reason to avoid it the way the old
daemon avoided running mpv continuously.

Fragmentation (`-frag_duration` alone — deliberately no `frag_keyframe`,
see _ffmpeg_cmd()'s own comment) is deliberately decoupled from the
source's keyframe/GOP interval, which isn't controllable and varies by
sender/encoder settings (this operator's OBS setup measures 8.333s via
ffprobe — see MAX_CACHED_FRAGMENTS/LATE_JOIN_KEYFRAME_TIMEOUT_SECONDS):
tying fragment emission to keyframes would inherit a full GOP of latency.
FRAG_DURATION_US is a threshold, not a hard cut — ffmpeg's muxer flushes
at the first frame crossing it, so real granularity is bounded by the
source's own frame interval regardless of how low this is set.

A client that connects mid-stream (e.g. the kiosk page reloading) needs
an init segment (ftyp+moov, emitted once via `empty_moov`) to bootstrap
its MediaSource, and enough fragments since the last keyframe for MSE to
actually have something decodable to start from — a fragment boundary
alone doesn't guarantee a keyframe, since most fragments here are far
smaller than a full GOP. This is handled by actually parsing each
fragment's moof/traf/tfhd/tfdt/trun boxes (see _parse_fragment_keyframe()
and friends) to know exactly which cached fragment, if any, contains the
most recent real keyframe — serve_client() replays starting from there,
and if the whole cache turns out to have none, waits for the next live
one instead of guessing. The same per-fragment keyframe timestamp is
also what lets srtStreamPlayer.js's trimBuffer() safely bound its
SourceBuffer.remove() calls to a real random-access point instead of an
arbitrary time offset.
"""
import asyncio
import re
import time
from collections import deque

from fastapi import WebSocket, WebSocketDisconnect

from . import srt_sink

# How often serve_client() reports its own per-client queue depth back to
# the browser (see that function's own comment).
QUEUE_REPORT_INTERVAL_SECONDS = 1.0

# Threshold, not a hard cut — see module docstring. Lower values reduce
# end-to-end latency at the cost of more frequent, smaller MSE appends on
# the client. If a periodic stall-then-jump reappears (audio playing
# smoothly while video freezes then snaps forward), check
# srtStreamPlayer.js's trimBuffer() first — its keyframe-aware remove()
# clamp is what actually fixes that failure mode. A genuine buffer
# underrun (audio AND video both freezing together — see
# srtStreamPlayer.js's TARGET_LATENCY_SECONDS) is a separate problem this
# value doesn't address.
FRAG_DURATION_US = 100_000
# How often the manager loop rechecks Settings > SRT Sink's enable
# toggle/passphrase — both while disabled (to notice it turning back on)
# and while a listener is already running (to notice it turning off, or
# the passphrase changing, and restart accordingly). Not a UDP poll
# interval — see module docstring for why there's no raw socket here.
CONFIG_POLL_INTERVAL_SECONDS = 2.0
# Bounds memory for the rolling cache. serve_client() locates the real
# keyframe within this window (see _parse_fragment_keyframe()) rather
# than assuming one is there, so this only needs to be "usually enough
# to replay instantly," not "guaranteed" — LATE_JOIN_KEYFRAME_TIMEOUT_SECONDS
# below is the actual correctness guarantee, this just controls how
# often a late join has to fall back to waiting for a live one.
#
# Tune _CACHE_COVERAGE_SECONDS, not MAX_CACHED_FRAGMENTS directly: the
# deque holds roughly one video AND one audio fragment per
# FRAG_DURATION_US interval, so a fixed fragment count silently covers
# less wall-clock time if that duration ever changes. Sized to match the
# margin given to LATE_JOIN_KEYFRAME_TIMEOUT_SECONDS below over the
# measured 8.333s GOP; a source with a longer GOP still falls back to the
# live-wait path correctly, just less often "instantly."
_CACHE_COVERAGE_SECONDS = 15
MAX_CACHED_FRAGMENTS = round(2 * _CACHE_COVERAGE_SECONDS / (FRAG_DURATION_US / 1_000_000))
# Bounded so one stalled client can't make the broadcast loop back up
# indefinitely; a client that falls this far behind is treated as
# unrecoverable and dropped rather than blocking everyone else.
CLIENT_QUEUE_MAXSIZE = 200
# How long serve_client() waits for a live keyframe when a joining
# client's cache window contains none at all (see its own comment). Past
# this, the join is abandoned (WebSocket closed with
# LATE_JOIN_TIMEOUT_CLOSE_CODE) rather than left hanging indefinitely;
# srtStreamPlayer.js retries on that specific code. Must safely exceed a
# full GOP with margin — a join can start right after a keyframe and need
# to wait nearly the whole interval for the next one. GOP length is
# source/encoder-dependent (measured at 8.333s for this operator's OBS
# setup) and not something this code can measure live, so this is sized
# generously rather than tightly.
LATE_JOIN_KEYFRAME_TIMEOUT_SECONDS = 15.0
# Application-defined WebSocket close code (the 4000-4999 range is
# reserved for exactly this) telling srtStreamPlayer.js's ws.onclose
# "this wasn't a normal disconnect, retry" — see LATE_JOIN_KEYFRAME_TIMEOUT_SECONDS.
LATE_JOIN_TIMEOUT_CLOSE_CODE = 4000

# Mirrors Settings > SRT Sink's "Debug overlay" toggle (srt_sink.py's
# debug_overlay field, same one srtStreamPlayer.js's overlay/event-dump
# logging is gated on) — set from run_forever()'s own config poll. Gates
# _pump_stdout()'s per-fragment logging below so steady-state operation
# doesn't spam journalctl; only turned on while actively chasing a stall.
_debug_enabled = False
# Debug-gated raw capture of ffmpeg's stdout (see _pump_stdout()) for
# hand-inspecting real tfhd/trun bytes on hardware — which encoding
# variant ffmpeg's muxer actually uses for sample sync flags was never
# confirmed before writing _parse_trun() below. Capped so leaving debug
# mode on doesn't grow this file unbounded.
DEBUG_CAPTURE_PATH = "/tmp/srt-fragment-capture.mp4"
DEBUG_CAPTURE_MAX_BYTES = 5_000_000


class _StreamState:
    def __init__(self):
        self.active = False
        self.mime_codec: str | None = None
        self.init_segment = b""
        # Each entry pairs a fragment's bytes with its own keyframe PTS in
        # seconds (None if it contains no video keyframe) — see
        # _parse_fragment_keyframe(). Lets serve_client() find exactly
        # which cached fragment to start a late join from, rather than
        # replaying the whole window and hoping.
        self.recent_fragments: deque[tuple[bytes, float | None]] = deque(maxlen=MAX_CACHED_FRAGMENTS)
        self.clients: set[asyncio.Queue] = set()
        # Learned once from the init segment's moov — see
        # _video_track_id_from_moov()/_video_timescale_from_moov(). Needed
        # to pick the video track's traf out of each moof (a moof commonly
        # has one per track) and to convert tfdt/trun's timescale-unit
        # arithmetic into seconds.
        self.video_track_id: int | None = None
        self.video_timescale: int | None = None
        # The most recent keyframe PTS (seconds) seen across all fragments
        # so far this stream — what's broadcast to clients for
        # trimBuffer()'s remove() clamp (see srtStreamPlayer.js).
        self.last_keyframe_pts: float | None = None
        # Incremental MP4 box parser state — see _feed_boxes().
        self._parse_buf = b""
        self._seen_moof = False
        self._current_fragment = bytearray()

    def reset(self):
        self.mime_codec = None
        self.init_segment = b""
        self.recent_fragments.clear()
        self.video_track_id = None
        self.video_timescale = None
        self.last_keyframe_pts = None
        self._parse_buf = b""
        self._seen_moof = False
        self._current_fragment = bytearray()


_state = _StreamState()


def is_playing() -> bool:
    return _state.active


def _redacted(cmd: list[str]) -> str:
    """The ffmpeg command line for the journal, minus passphrases (SRT's
    rides in the URL, RIST's in -secret)."""
    out = []
    for i, arg in enumerate(cmd):
        if i and cmd[i - 1] == "-secret":
            arg = "***"
        out.append(re.sub(r"passphrase=[^&]*", "passphrase=***", arg))
    return " ".join(out)


# --- Generic box-tree helpers / keyframe/sync-sample parsing ----------
#
# Added to fix two bugs traced to the same root cause: nothing in this
# pipeline previously knew where real video keyframes actually were.
# 1. srtStreamPlayer.js's trimBuffer() periodically called
#    SourceBuffer.remove() at an arbitrary time offset, which — since our
#    fragments aren't keyframe-aligned (see _ffmpeg_cmd()'s own comment)
#    — could land with no clean random-access point for Chromium to
#    split on; confirmed on hardware this was wiping the ENTIRE buffered
#    range instead of the requested sliver, causing periodic freezes.
# 2. A joining client's cached-fragment replay (see serve_client()) used
#    to just be "statistically" likely to contain a keyframe; when it
#    didn't, playback silently never started.
#
# Below reads exactly enough of moof/traf/tfhd/tfdt/trun (ISO/IEC
# 14496-12 §8.8) to find each fragment's first real sync sample and its
# presentation timestamp, both fed back to the two call sites above. The
# same video-trak lookup this needs (_video_trak_payload()) is also what
# _mime_codec_from_moov() below uses to build a codec string for
# whichever of H.264/H.265/AV1 the source actually sent.
# Every function here is pure (bytes in, value out) and independently
# testable without ffmpeg/SRT/a browser — see test_srt_stream_bridge_boxes.py.


def _iter_child_boxes(data: bytes):
    """Yields (box_type, box_payload) for each top-level child box in
    `data`, which must already be a complete, fully-buffered region (a
    moov/trak/moof/traf's own contents — never a streaming/partial
    buffer, unlike _feed_boxes()'s own top-level splitting, which reads
    incrementally off ffmpeg's stdout instead)."""
    pos = 0
    n = len(data)
    while pos + 8 <= n:
        size = int.from_bytes(data[pos:pos + 4], "big")
        if size < 8 or pos + size > n:
            break
        yield data[pos + 4:pos + 8], data[pos + 8:pos + size]
        pos += size


def _find_child_box(data: bytes, want_type: bytes) -> bytes | None:
    for box_type, payload in _iter_child_boxes(data):
        if box_type == want_type:
            return payload
    return None


def _video_trak_payload(moov_payload: bytes) -> bytes | None:
    """Locates the `trak` whose handler is video ('vide') — needed so
    _video_track_id_from_moov()/_video_timescale_from_moov() don't
    accidentally read the audio track's tkhd/mdhd on a source with more
    than one trak. `hdlr`'s handler_type sits at a fixed payload offset
    (version_flags(4)+pre_defined(4) = byte 8, then the 4-byte type code)
    regardless of version, so a raw byte-search for the tag within each
    candidate trak's own byte range is safe here — same pragmatic
    shortcut the codec-string builders below take for avcC/hvcC/av1C."""
    for box_type, trak_payload in _iter_child_boxes(moov_payload):
        if box_type != b"trak":
            continue
        idx = trak_payload.find(b"hdlr")
        if idx == -1 or idx + 16 > len(trak_payload):
            continue
        if trak_payload[idx + 12:idx + 16] == b"vide":
            return trak_payload
    return None


def _version_prefixed_field_offset(version_flags_first_byte: int) -> int:
    """tkhd and mdhd share the same leading layout: version_flags(4),
    then two time fields (creation_time/modification_time) before the
    field each caller actually wants (track_ID for tkhd, timescale for
    mdhd) — 4 bytes each in version 0, 8 bytes each in version 1."""
    return 4 + (16 if version_flags_first_byte == 1 else 8)


def _video_track_id_from_moov(init_segment: bytes) -> int | None:
    """Reads the video trak's tkhd.track_ID — needed to match each
    moof's traf boxes (keyed by tfhd.track_id) to the video track
    specifically, since a moof here commonly has one traf per track and
    only the video one's samples carry keyframe/sync-sample semantics we
    care about. Returns None (never raises) if anything doesn't parse as
    expected — callers must treat that as 'can't determine, be
    conservative', not 'no video track'."""
    try:
        moov_payload = _find_child_box(init_segment, b"moov")
        trak_payload = _video_trak_payload(moov_payload) if moov_payload else None
        tkhd = _find_child_box(trak_payload, b"tkhd") if trak_payload else None
        if not tkhd:
            return None
        offset = _version_prefixed_field_offset(tkhd[0])
        if offset + 4 > len(tkhd):
            return None
        return int.from_bytes(tkhd[offset:offset + 4], "big")
    except Exception:
        return None


def _video_timescale_from_moov(init_segment: bytes) -> int | None:
    """Reads the video trak's mdhd.timescale — needed to convert
    tfdt/trun's timescale-unit arithmetic into seconds. Same
    can't-determine-vs-none-found caveat as _video_track_id_from_moov()."""
    try:
        moov_payload = _find_child_box(init_segment, b"moov")
        trak_payload = _video_trak_payload(moov_payload) if moov_payload else None
        mdia_payload = _find_child_box(trak_payload, b"mdia") if trak_payload else None
        mdhd = _find_child_box(mdia_payload, b"mdhd") if mdia_payload else None
        if not mdhd:
            return None
        offset = _version_prefixed_field_offset(mdhd[0])
        if offset + 4 > len(mdhd):
            return None
        return int.from_bytes(mdhd[offset:offset + 4], "big")
    except Exception:
        return None


def _avc1_codec_string(video_trak_payload: bytes) -> str | None:
    """"avc1.PPCCLL" straight out of the avcC box's
    AVCDecoderConfigurationRecord (profile_idc/profile_compatibility/
    level_idc are bytes 1-3 of its payload). A raw byte-search for the
    'avcC' tag (rather than walking the full mdia->minf->stbl->stsd->
    avc1->avcC box tree) is a pragmatic shortcut: avcC is a well-defined
    leaf box and a spurious collision with its 4-byte tag elsewhere in a
    well-formed trak isn't a practical concern."""
    idx = video_trak_payload.find(b"avcC")
    if idx == -1 or idx + 7 >= len(video_trak_payload):
        return None
    profile, compat, level = video_trak_payload[idx + 5], video_trak_payload[idx + 6], video_trak_payload[idx + 7]
    return f"avc1.{profile:02x}{compat:02x}{level:02x}"


def _reverse_bits32(value: int) -> int:
    """RFC 6381's HEVC codec string represents
    general_profile_compatibility_flags with each bit read out in
    reversed order (bit 0 of the wire value is treated as the most
    significant bit of the number that gets hex-formatted) — an
    ISO/IEC 14496-15 quirk inherited directly from how HEVC's own spec
    indexes those flags, not a mistake to "simplify" away."""
    result = 0
    for _ in range(32):
        result = (result << 1) | (value & 1)
        value >>= 1
    return result


def _hevc_codec_string(video_trak_payload: bytes, sample_entry_tag: str) -> str | None:
    """Builds an 'hev1.…'/'hvc1.…' codec string per RFC 6381 §3.4 (the
    HEVC-in-ISOBMFF carriage spec), read out of the hvcC box's
    HEVCDecoderConfigurationRecord (ISO/IEC 14496-15 §8.3.3.1) — same
    pragmatic raw-byte-search shortcut as _avc1_codec_string(). Written
    directly against the spec; this pipeline has never actually carried
    an HEVC source (ffmpeg's `-c:v copy` in _ffmpeg_cmd() passes through
    whatever codec the SRT source sends, but only H.264 has been seen on
    hardware) — see test_srt_stream_bridge_boxes.py for fixtures
    exercising the bit-layout math, not a real capture.

    `sample_entry_tag` ('hev1' or 'hvc1', whichever the source's stsd
    actually uses) becomes the codec string's own prefix — it isn't
    cosmetic: hvc1 vs hev1 changes whether MediaSource expects parameter
    sets in-band per sample or only out-of-band in hvcC, so reporting
    the wrong one for what's actually being sent would misdeclare the
    codec, not just its profile."""
    idx = video_trak_payload.find(b"hvcC")
    if idx == -1:
        return None
    payload = video_trak_payload[idx + 4:]
    if len(payload) < 13:
        return None
    profile_byte = payload[1]
    profile_space = (profile_byte >> 6) & 0x3
    tier_flag = (profile_byte >> 5) & 0x1
    profile_idc = profile_byte & 0x1F
    compat_flags = int.from_bytes(payload[2:6], "big")
    constraint_flags = payload[6:12]
    level_idc = payload[12]

    space_prefix = {0: "", 1: "A", 2: "B", 3: "C"}[profile_space]
    compat_hex = format(_reverse_bits32(compat_flags), "x")
    tier_char = "H" if tier_flag else "L"
    # Trailing all-zero bytes are dropped (and the whole field omitted
    # if every byte is zero) per RFC 6381's own formatting rule.
    constraint_part = constraint_flags.rstrip(b"\x00").hex().upper()

    parts = [sample_entry_tag, f"{space_prefix}{profile_idc}", compat_hex, f"{tier_char}{level_idc}"]
    if constraint_part:
        parts.append(constraint_part)
    return ".".join(parts)


def _av1_codec_string(video_trak_payload: bytes) -> str | None:
    """Builds an 'av01.P.LLT.DD' codec string per the AV1 Codec ISO
    Media File Format Binding spec's "codecs parameter string" section,
    read out of the av1C box's AV1CodecConfigurationRecord. Same
    never-seen-on-real-hardware caveat as _hevc_codec_string() — written
    directly against the spec, not validated against a real AV1 capture."""
    idx = video_trak_payload.find(b"av1C")
    if idx == -1:
        return None
    payload = video_trak_payload[idx + 4:]
    if len(payload) < 4:
        return None
    seq_profile = (payload[1] >> 5) & 0x7
    seq_level_idx_0 = payload[1] & 0x1F
    seq_tier_0 = (payload[2] >> 7) & 0x1
    high_bitdepth = (payload[2] >> 6) & 0x1
    twelve_bit = (payload[2] >> 5) & 0x1

    if not high_bitdepth:
        bit_depth = 8
    elif seq_profile == 2 and twelve_bit:
        bit_depth = 12
    else:
        bit_depth = 10
    tier_char = "H" if seq_tier_0 else "M"
    return f"av01.{seq_profile}.{seq_level_idx_0:02d}{tier_char}.{bit_depth:02d}"


def _video_codec_string(video_trak_payload: bytes) -> str | None:
    """Dispatches to whichever codec's decoder-config box is actually
    present in the video track — the config box tag itself (avcC/hvcC/
    av1C) is the authoritative signal for which codec is in use, not the
    sample-entry tag (hev1 vs hvc1 both carry an hvcC, for instance)."""
    if b"hvcC" in video_trak_payload:
        tag = "hvc1" if b"hvc1" in video_trak_payload else "hev1"
        return _hevc_codec_string(video_trak_payload, tag)
    if b"av1C" in video_trak_payload:
        return _av1_codec_string(video_trak_payload)
    return _avc1_codec_string(video_trak_payload)


def _mime_codec_from_moov(init_segment: bytes) -> str | None:
    """Builds the exact codecs string MediaSource.addSourceBuffer() needs
    — it must list every track present in the segments, or appendBuffer()
    rejects them outright. Hardcoding one video profile/audio codec would
    break for any source that doesn't happen to match it, so both are
    read out of the moov's own box contents rather than assumed:

    - video: whichever codec the source actually used (H.264/H.265/AV1
      — see _video_codec_string()), scoped to the video trak specifically
      (reusing _video_trak_payload(), the same lookup
      _video_track_id_from_moov()/_video_timescale_from_moov() use for
      the keyframe-parsing work) rather than searching the whole moov,
      so this can't accidentally pick up an audio-side box.
    - audio: presence of an 'mp4a' box means an AAC track is present (the
      near-universal choice for this kind of source — OBS/screen-share
      encoders default to it), reported as "mp4a.40.2" (AAC-LC) without
      reading the exact audioObjectType out of the esds box's bit-packed
      DecoderSpecificInfo — a source encoding some other AAC profile
      would need that read done properly; not attempted here.
    """
    moov_payload = _find_child_box(init_segment, b"moov")
    video_trak_payload = _video_trak_payload(moov_payload) if moov_payload else None
    if video_trak_payload is None:
        return None
    video_codec = _video_codec_string(video_trak_payload)
    if video_codec is None:
        return None
    codecs = [video_codec]
    if b"mp4a" in init_segment:
        codecs.append("mp4a.40.2")
    return f'video/mp4; codecs="{",".join(codecs)}"'


def _parse_tfhd(payload: bytes) -> dict:
    """Reads a tfhd box's track_id plus, if present, default_sample_duration/
    default_sample_flags — the other optional fields (base_data_offset,
    sample_description_index, default_sample_size) are skipped over by
    their tf_flags presence bits alone; their values aren't needed here.
    See ISO/IEC 14496-12 §8.8.7."""
    tf_flags = int.from_bytes(payload[0:4], "big") & 0x00FFFFFF
    track_id = int.from_bytes(payload[4:8], "big")
    pos = 8
    if tf_flags & 0x000001:  # base_data_offset
        pos += 8
    if tf_flags & 0x000002:  # sample_description_index
        pos += 4
    default_sample_duration = None
    if tf_flags & 0x000008:  # default_sample_duration
        default_sample_duration = int.from_bytes(payload[pos:pos + 4], "big")
        pos += 4
    if tf_flags & 0x000010:  # default_sample_size
        pos += 4
    default_sample_flags = None
    if tf_flags & 0x000020:  # default_sample_flags
        default_sample_flags = int.from_bytes(payload[pos:pos + 4], "big")
    return {
        "track_id": track_id,
        "default_sample_duration": default_sample_duration,
        "default_sample_flags": default_sample_flags,
    }


def _parse_tfdt(payload: bytes) -> int:
    """base_media_decode_time — 32-bit in version 0, 64-bit in version 1
    (version byte at payload offset 0). See ISO/IEC 14496-12 §8.8.12."""
    if payload[0] == 1:
        return int.from_bytes(payload[4:12], "big")
    return int.from_bytes(payload[4:8], "big")


# sample_is_non_sync_sample bit within a resolved 32-bit sample_flags
# value (ISO/IEC 14496-12 §8.8.3.1) — 0 means the sample IS a sync
# sample (a real keyframe).
_SAMPLE_IS_NON_SYNC_SAMPLE_BIT = 0x00010000


def _parse_trun(
    payload: bytes, default_sample_flags: int | None, default_sample_duration: int | None
) -> tuple[int | None, list[int]]:
    """Walks one trun box's sample table. Returns (index of the first
    sync sample found, or None; each sample's resolved duration, so a
    caller can sum durations up to that index to get its presentation
    time offset within the fragment).

    Resolves each sample's sync-sample-ness with this priority, matching
    every way ffmpeg's fmp4 muxer can legally encode it (ISO/IEC
    14496-12 §8.8.8.2) — nobody has confirmed on real hardware which of
    these this specific muxer actually uses, so all three are
    implemented rather than assumed:
      1. first_sample_flags (trun flag 0x000004) — applies ONLY to
         sample 0, overriding both of the below for it.
      2. this sample's own per-sample sample_flags (trun flag 0x000400).
      3. tfhd's default_sample_flags (same value for every sample).
    If none apply to a given sample, its sync-ness can't be determined —
    treated as non-sync, the conservative direction (see module note:
    missing a real keyframe is safe, claiming a fake one isn't)."""
    trun_flags = int.from_bytes(payload[0:4], "big") & 0x00FFFFFF
    sample_count = int.from_bytes(payload[4:8], "big")
    pos = 8
    if trun_flags & 0x000001:  # data_offset
        pos += 4
    first_sample_flags = None
    if trun_flags & 0x000004:  # first_sample_flags
        first_sample_flags = int.from_bytes(payload[pos:pos + 4], "big")
        pos += 4
    has_duration = bool(trun_flags & 0x000100)
    has_size = bool(trun_flags & 0x000200)
    has_flags = bool(trun_flags & 0x000400)
    has_cto = bool(trun_flags & 0x000800)

    durations: list[int] = []
    keyframe_index: int | None = None
    for i in range(sample_count):
        if has_duration:
            if pos + 4 > len(payload):
                break
            duration = int.from_bytes(payload[pos:pos + 4], "big")
            pos += 4
        else:
            duration = default_sample_duration
        if has_size:
            pos += 4
        per_sample_flags = None
        if has_flags:
            if pos + 4 > len(payload):
                break
            per_sample_flags = int.from_bytes(payload[pos:pos + 4], "big")
            pos += 4
        if has_cto:
            pos += 4

        if i == 0 and first_sample_flags is not None:
            sample_flags = first_sample_flags
        elif per_sample_flags is not None:
            sample_flags = per_sample_flags
        else:
            sample_flags = default_sample_flags

        durations.append(duration if duration is not None else 0)
        if (
            keyframe_index is None
            and sample_flags is not None
            and not (sample_flags & _SAMPLE_IS_NON_SYNC_SAMPLE_BIT)
        ):
            keyframe_index = i
    return keyframe_index, durations


def _parse_fragment_keyframe(
    fragment: bytes, video_track_id: int | None, video_timescale: int | None
) -> float | None:
    """Given one completed moof+mdat fragment (as produced by
    _feed_boxes()), returns the presentation timestamp in seconds of its
    first real video keyframe, or None if it doesn't contain one — or if
    anything here couldn't be determined (unexpected box layout, missing
    video track info, etc.). Never raises: `None` always means "no new
    information," which every caller must treat conservatively (don't
    assume "no keyframe," just "don't know")."""
    if video_track_id is None or video_timescale is None:
        return None
    try:
        moof_payload = _find_child_box(fragment, b"moof")
        if moof_payload is None:
            return None
        for box_type, traf_payload in _iter_child_boxes(moof_payload):
            if box_type != b"traf":
                continue
            tfhd_payload = _find_child_box(traf_payload, b"tfhd")
            if tfhd_payload is None:
                continue
            tfhd = _parse_tfhd(tfhd_payload)
            if tfhd["track_id"] != video_track_id:
                continue
            tfdt_payload = _find_child_box(traf_payload, b"tfdt")
            trun_payload = _find_child_box(traf_payload, b"trun")
            if tfdt_payload is None or trun_payload is None:
                return None
            base_decode_time = _parse_tfdt(tfdt_payload)
            keyframe_index, durations = _parse_trun(
                trun_payload, tfhd["default_sample_flags"], tfhd["default_sample_duration"]
            )
            if keyframe_index is None:
                return None
            offset_units = sum(durations[:keyframe_index])
            return (base_decode_time + offset_units) / video_timescale
        return None
    except Exception:
        return None


def _feed_boxes(chunk: bytes) -> list[tuple[bytes, float | None]]:
    """Incrementally splits ffmpeg's raw stdout byte stream into top-level
    MP4 boxes, and returns the box-aligned units (the init segment, once;
    each subsequent completed fragment, moof+mdat) newly completed by this
    call, ready to broadcast.

    Forwarding whatever raw, arbitrarily-sized slice comes back from
    proc.stdout.read() would break a client connecting mid-stream: the
    very next live chunk after its cached-fragment replay could land
    mid-fragment (a fragment only a couple hundred ms long is very likely
    still in progress when a client joins), handing it a byte range with
    no moof header at its start — Chromium's demuxer has no tolerance for
    that ("stream parsing failed"). Routing every broadcast through this
    same box-aligned unit list — the exact same units a joining client's
    cached replay uses — means no client, old or new, ever receives a
    partial box.

    Each returned unit is paired with its keyframe PTS (seconds), or
    None — the init segment and any fragment without a determinable
    video keyframe both get None (see _parse_fragment_keyframe()); this
    is what serve_client() uses to find a correct late-join replay start
    and what feeds _state.last_keyframe_pts for trimBuffer()'s clamp."""
    ready: list[tuple[bytes, float | None]] = []
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
                ready.append((bytes(_state.init_segment), None))
            else:
                _state.init_segment += box
                if box_type == b"moov":
                    _state.mime_codec = _mime_codec_from_moov(_state.init_segment)
                    _state.video_track_id = _video_track_id_from_moov(_state.init_segment)
                    _state.video_timescale = _video_timescale_from_moov(_state.init_segment)
        else:
            if box_type == b"moof":
                fragment = bytes(_state._current_fragment)
                keyframe_pts = _parse_fragment_keyframe(
                    fragment, _state.video_track_id, _state.video_timescale
                )
                if keyframe_pts is not None:
                    _state.last_keyframe_pts = keyframe_pts
                _state.recent_fragments.append((fragment, keyframe_pts))
                ready.append((fragment, keyframe_pts))
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


def _broadcast(item: tuple[bytes, float | None]) -> None:
    """`item` is a (fragment_bytes, keyframe_pts) pair, same shape as
    _feed_boxes()'s own return list — passed through to each client's
    queue as-is so serve_client() can tell, while draining it, whether a
    given fragment is safe to start a late join from (see its own
    comment)."""
    stale = []
    for queue in _state.clients:
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            stale.append(queue)
    for queue in stale:
        _state.clients.discard(queue)
        _end_client(queue)


def _ffmpeg_cmd(input_args: list[str]) -> list[str]:
    """`input_args` is srt_sink.listener_input_args() for the configured
    receiver mode (SRT, RIST unicast or RIST multicast) — everything from
    here on is the same remux pipeline regardless of transport."""
    return [
        "ffmpeg",
        "-loglevel", "warning", "-nostats",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        *input_args,
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
    # Wall-clock (not monotonic) so lines here line up by eye with
    # journalctl's own per-line timestamps and with the wall-clock stamps
    # srtStreamPlayer.js's debug event dump carries — the two logs are
    # meant to be read side by side while chasing a stall.
    last_read_started_at = None
    last_fragment_at = None
    # Debug-gated raw capture of ffmpeg's stdout, for hand-inspecting
    # real tfhd/trun bytes on hardware — see DEBUG_CAPTURE_PATH's own
    # comment. Opened lazily the first time debug mode is on, closed
    # (and reopened fresh next time) the moment it's turned back off, so
    # a capture is always one contiguous, recent session rather than an
    # accumulation across unrelated debugging sessions.
    debug_capture_file = None
    debug_capture_written = 0
    try:
        while True:
            read_started_at = time.time()
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            if not validated:
                validated = True
                _state.active = True
                print("[srt-stream-bridge] validated — stream is live", flush=True)
            if _debug_enabled:
                # How long this read() blocked waiting on ffmpeg's stdout —
                # a spike here (ffmpeg had nothing to hand back) points
                # upstream of this process entirely (SRT/network jitter,
                # the encoder itself), as opposed to a stall introduced
                # afterwards in broadcast/append/render.
                stall_ms = (read_started_at - last_read_started_at) * 1000 if last_read_started_at else None
                wait_str = f"{stall_ms:.1f}" if stall_ms is not None else "n/a"
                print(
                    f"[srt-stream-bridge] debug read bytes={len(chunk)} stdout_wait_ms={wait_str}",
                    flush=True,
                )
                if debug_capture_file is None:
                    try:
                        debug_capture_file = open(DEBUG_CAPTURE_PATH, "wb")
                        debug_capture_written = 0
                        print(f"[srt-stream-bridge] debug capturing raw stream to {DEBUG_CAPTURE_PATH}", flush=True)
                    except OSError as err:
                        print(f"[srt-stream-bridge] debug capture failed to open: {err}", flush=True)
                if debug_capture_file is not None and debug_capture_written < DEBUG_CAPTURE_MAX_BYTES:
                    debug_capture_file.write(chunk)
                    debug_capture_written += len(chunk)
                    if debug_capture_written >= DEBUG_CAPTURE_MAX_BYTES:
                        print(
                            f"[srt-stream-bridge] debug capture reached {DEBUG_CAPTURE_MAX_BYTES} bytes, stopping",
                            flush=True,
                        )
            elif debug_capture_file is not None:
                debug_capture_file.close()
                debug_capture_file = None
            last_read_started_at = read_started_at
            units = _feed_boxes(chunk)
            if _debug_enabled:
                now = time.time()
                for fragment, keyframe_pts in units:
                    gap_ms = (now - last_fragment_at) * 1000 if last_fragment_at else None
                    depths = [q.qsize() for q in _state.clients]
                    gap_str = f"{gap_ms:.1f}" if gap_ms is not None else "n/a"
                    kf_str = f"{keyframe_pts:.3f}" if keyframe_pts is not None else "none"
                    print(
                        f"[srt-stream-bridge] debug fragment bytes={len(fragment)} "
                        f"since_prev_fragment_ms={gap_str} keyframe_pts={kf_str} client_queue_depths={depths}",
                        flush=True,
                    )
                    last_fragment_at = now
            for unit in units:
                _broadcast(unit)
    finally:
        if debug_capture_file is not None:
            debug_capture_file.close()
        _state.active = False
        for queue in list(_state.clients):
            _end_client(queue)
        print(
            f"[srt-stream-bridge] ffmpeg exited code={proc.returncode}. "
            f"Last stderr: {list(stderr_tail)}",
            flush=True,
        )


async def run_forever():
    """Keeps exactly one ffmpeg listener running for as long as
    Settings > LAN Video Receiver is enabled, (re)launching it whenever it
    isn't running yet, and restarting it whenever it exits on its own or
    any input setting changes underneath it (mode, passphrase, latency/
    buffer, multicast group…) — none of those can be adjusted on a live
    SRT/RIST receiver, only applied to a fresh one. "Changed" is simply
    srt_sink.listener_input_args() coming out different."""
    proc = None
    pump_task = None
    stderr_task = None
    active_input_args = None

    async def _teardown():
        nonlocal proc, pump_task, stderr_task, active_input_args
        if proc is not None and proc.returncode is None:
            proc.kill()
        if pump_task is not None:
            await pump_task
        if stderr_task is not None:
            stderr_task.cancel()
        proc = pump_task = stderr_task = None
        active_input_args = None

    was_enabled = None
    try:
        while True:
            config = srt_sink.read_config()
            enabled = srt_sink.effective_enabled(config)
            if enabled != was_enabled:
                print(f"[srt-stream-bridge] effective_enabled={enabled}", flush=True)
                was_enabled = enabled
            global _debug_enabled
            _debug_enabled = config["debug_overlay"]
            input_args = srt_sink.listener_input_args(config) if enabled else None

            if proc is not None and (not enabled or input_args != active_input_args):
                print("[srt-stream-bridge] config changed, restarting listener", flush=True)
                await _teardown()

            if not enabled:
                await asyncio.sleep(CONFIG_POLL_INTERVAL_SECONDS)
                continue

            if proc is None:
                _state.reset()
                cmd = _ffmpeg_cmd(input_args)
                print(f"[srt-stream-bridge] starting {config['mode']} listener: {_redacted(cmd)}", flush=True)
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                stderr_tail: deque = deque(maxlen=20)
                stderr_task = asyncio.create_task(_drain_stderr(proc, stderr_tail))
                pump_task = asyncio.create_task(_pump_stdout(proc, stderr_tail))
                active_input_args = input_args

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
    cached = list(_state.recent_fragments)
    _state.clients.add(queue)

    # Find the most recent cached fragment that actually contains a real
    # video keyframe — replay starts there, discarding older cached
    # fragments the decoder couldn't have used as a starting point
    # anyway. This used to just replay the whole cache and hope one of
    # them had a keyframe (see module docstring); if none of them do
    # (an unlucky join, or the stream just started), replay nothing and
    # fall into "waiting for a live one" below instead of guessing.
    replay_start = next(
        (i for i in range(len(cached) - 1, -1, -1) if cached[i][1] is not None), None
    )
    replay_fragments = [f for f, _ in cached[replay_start:]] if replay_start is not None else []
    waiting_for_keyframe = replay_start is None

    try:
        if mime_codec:
            await websocket.send_json({"mimeCodec": mime_codec})
        await websocket.send_bytes(init_segment)
        for fragment in replay_fragments:
            await websocket.send_bytes(fragment)

        # Periodically reports this client's own queue depth (and the
        # latest confirmed keyframe timestamp, for srtStreamPlayer.js's
        # trimBuffer() clamp) back over the same connection — added to
        # check a real blind spot: this queue sits between ffmpeg's
        # output and the browser, and `websocket.send_bytes()` applies
        # real backpressure if the browser is ever slow to read,
        # regardless of whether either side's CPU shows it. The
        # client-side append queue alone can't reveal that, since it
        # only sees what's already arrived. Folded into this same single
        # send loop (not a separate task) since concurrent sends on one
        # WebSocket aren't safe to interleave.
        last_report = time.monotonic()
        # Only set while waiting_for_keyframe — bounds how long a client
        # with no keyframe in its cache window sits with nothing to show
        # before giving up, rather than hanging on a stuck join forever.
        # See LATE_JOIN_KEYFRAME_TIMEOUT_SECONDS's own comment.
        join_deadline = time.monotonic() + LATE_JOIN_KEYFRAME_TIMEOUT_SECONDS if waiting_for_keyframe else None
        while True:
            remaining = QUEUE_REPORT_INTERVAL_SECONDS - (time.monotonic() - last_report)
            if join_deadline is not None:
                remaining = min(remaining, join_deadline - time.monotonic())
            try:
                item = await asyncio.wait_for(queue.get(), timeout=max(remaining, 0.001))
            except asyncio.TimeoutError:
                if join_deadline is not None and time.monotonic() >= join_deadline:
                    print(
                        "[srt-stream-bridge] client gave up waiting for a keyframe after "
                        f"{LATE_JOIN_KEYFRAME_TIMEOUT_SECONDS:.0f}s, closing",
                        flush=True,
                    )
                    await websocket.close(code=LATE_JOIN_TIMEOUT_CLOSE_CODE, reason="no keyframe")
                    return
                await websocket.send_json({"queueDepth": queue.qsize(), "keyframeTime": _state.last_keyframe_pts})
                last_report = time.monotonic()
                continue
            if item is None:
                break
            fragment, keyframe_pts = item
            if waiting_for_keyframe:
                if keyframe_pts is None:
                    # Not independently decodable — drop it and keep
                    # waiting rather than hand the client something it
                    # can't start MediaSource from.
                    continue
                waiting_for_keyframe = False
                join_deadline = None
            await websocket.send_bytes(fragment)
            if time.monotonic() - last_report >= QUEUE_REPORT_INTERVAL_SECONDS:
                await websocket.send_json({"queueDepth": queue.qsize(), "keyframeTime": _state.last_keyframe_pts})
                last_report = time.monotonic()
    except WebSocketDisconnect:
        pass
    finally:
        _state.clients.discard(queue)
