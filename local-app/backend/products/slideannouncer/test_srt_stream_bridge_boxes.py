"""Unit tests for the pure MP4 box-parsing functions in
srt_stream_bridge.py — _parse_tfhd/_parse_tfdt/_parse_trun,
_video_track_id_from_moov/_video_timescale_from_moov, and the
orchestrating _parse_fragment_keyframe(). All hand-crafted byte
fixtures, no ffmpeg/SRT/browser needed.

Real hardware capture should eventually add a couple of actual captured
fragments here too (see srt_stream_bridge.py's DEBUG_CAPTURE_PATH) to
pin down which trun encoding variant ffmpeg's muxer actually emits —
these hand-crafted fixtures only prove the parser handles each variant
ISO/IEC 14496-12 allows, not which one is real.
"""
from products.slideannouncer import srt_stream_bridge as bridge

# sample_is_non_sync_sample bit — 0 means "is a sync sample" (a keyframe).
NON_SYNC = 0x00010000
SYNC_FLAGS = 0x02000000  # arbitrary bits elsewhere set, non-sync bit clear
NON_SYNC_FLAGS = 0x01010000  # arbitrary bits elsewhere set, non-sync bit set


def box(box_type: bytes, payload: bytes) -> bytes:
    return (len(payload) + 8).to_bytes(4, "big") + box_type + payload


def u32(value: int) -> bytes:
    return value.to_bytes(4, "big")


def i32(value: int) -> bytes:
    return value.to_bytes(4, "big", signed=True)


# --- tfhd ---------------------------------------------------------------


def test_parse_tfhd_track_id_only():
    payload = u32(0x000000) + u32(7)  # version_flags (no optional fields), track_id=7
    result = bridge._parse_tfhd(payload)
    assert result["track_id"] == 7
    assert result["default_sample_duration"] is None
    assert result["default_sample_flags"] is None


def test_parse_tfhd_duration_and_flags_present():
    tf_flags = 0x000008 | 0x000020  # default_sample_duration, default_sample_flags
    payload = u32(tf_flags) + u32(3) + u32(1000) + u32(SYNC_FLAGS)
    result = bridge._parse_tfhd(payload)
    assert result["track_id"] == 3
    assert result["default_sample_duration"] == 1000
    assert result["default_sample_flags"] == SYNC_FLAGS


def test_parse_tfhd_skips_unused_optional_fields():
    # base_data_offset(8) + sample_description_index(4) + default_sample_size(4)
    # present but unread — only their presence bits matter, to get past
    # them to default_sample_flags correctly.
    tf_flags = 0x000001 | 0x000002 | 0x000010 | 0x000020
    payload = (
        u32(tf_flags) + u32(9)
        + (0).to_bytes(8, "big")  # base_data_offset
        + u32(1)  # sample_description_index
        + u32(500)  # default_sample_size
        + u32(SYNC_FLAGS)  # default_sample_flags
    )
    result = bridge._parse_tfhd(payload)
    assert result["track_id"] == 9
    assert result["default_sample_flags"] == SYNC_FLAGS


# --- tfdt ---------------------------------------------------------------


def test_parse_tfdt_version0():
    payload = bytes([0, 0, 0, 0]) + u32(48000)
    assert bridge._parse_tfdt(payload) == 48000


def test_parse_tfdt_version1_64bit():
    big_time = 1_000_000_000_000
    payload = bytes([1, 0, 0, 0]) + big_time.to_bytes(8, "big")
    assert bridge._parse_tfdt(payload) == big_time


# --- trun -----------------------------------------------------------------


def test_parse_trun_first_sample_flags_overrides_per_sample():
    # trun_flags: data_offset | first_sample_flags | duration | per-sample flags
    trun_flags = 0x000001 | 0x000004 | 0x000100 | 0x000400
    payload = (
        u32(trun_flags) + u32(2)  # sample_count=2
        + u32(0)  # data_offset
        + u32(SYNC_FLAGS)  # first_sample_flags — sample 0 IS sync
        # sample 0: per-sample flags say non-sync, but first_sample_flags wins
        + u32(1000) + u32(NON_SYNC_FLAGS)
        # sample 1: per-sample flags say non-sync, no override applies
        + u32(1000) + u32(NON_SYNC_FLAGS)
    )
    keyframe_index, durations = bridge._parse_trun(payload, None, None)
    assert keyframe_index == 0
    assert durations == [1000, 1000]


def test_parse_trun_per_sample_flags_tier():
    trun_flags = 0x000100 | 0x000400  # duration + per-sample flags, no first_sample_flags
    payload = (
        u32(trun_flags) + u32(2)
        + u32(1000) + u32(NON_SYNC_FLAGS)  # sample 0: not sync
        + u32(1000) + u32(SYNC_FLAGS)  # sample 1: sync
    )
    keyframe_index, durations = bridge._parse_trun(payload, None, None)
    assert keyframe_index == 1
    assert durations == [1000, 1000]


def test_parse_trun_falls_back_to_tfhd_default_flags():
    trun_flags = 0x000100  # duration only — no per-sample/first-sample flags at all
    payload = u32(trun_flags) + u32(2) + u32(1000) + u32(1000)
    keyframe_index, durations = bridge._parse_trun(payload, SYNC_FLAGS, None)
    assert keyframe_index == 0  # every sample resolves via tfhd default -> first one wins
    assert durations == [1000, 1000]


def test_parse_trun_no_flags_available_is_conservative():
    trun_flags = 0x000100
    payload = u32(trun_flags) + u32(1) + u32(1000)
    keyframe_index, durations = bridge._parse_trun(payload, None, None)
    assert keyframe_index is None
    assert durations == [1000]


def test_parse_trun_size_and_composition_offset_are_skipped_correctly():
    # duration | size | per-sample flags | composition_offset (signed, version 1)
    trun_flags = 0x000100 | 0x000200 | 0x000400 | 0x000800
    version_flags = (1 << 24) | trun_flags
    payload = (
        version_flags.to_bytes(4, "big") + u32(1)
        + u32(500)  # duration
        + u32(12345)  # size
        + u32(SYNC_FLAGS)  # sample_flags
        + i32(-100)  # signed composition time offset
    )
    keyframe_index, durations = bridge._parse_trun(payload, None, None)
    assert keyframe_index == 0
    assert durations == [500]


def test_parse_trun_uses_default_duration_when_absent():
    trun_flags = 0x000400  # flags only, no per-sample duration
    payload = u32(trun_flags) + u32(2) + u32(NON_SYNC_FLAGS) + u32(SYNC_FLAGS)
    keyframe_index, durations = bridge._parse_trun(payload, None, default_sample_duration=750)
    assert keyframe_index == 1
    assert durations == [750, 750]


# --- moov / trak / hdlr / tkhd / mdhd ------------------------------------


def _hdlr(handler_type: bytes) -> bytes:
    payload = u32(0) + u32(0) + handler_type + bytes(12) + b"\x00"
    return box(b"hdlr", payload)


def _tkhd(track_id: int, version: int = 0) -> bytes:
    if version == 1:
        payload = bytes([1, 0, 0, 0]) + bytes(8) + bytes(8) + u32(track_id)
    else:
        payload = bytes([0, 0, 0, 0]) + bytes(4) + bytes(4) + u32(track_id)
    return box(b"tkhd", payload)


def _mdhd(timescale: int, version: int = 0) -> bytes:
    if version == 1:
        payload = bytes([1, 0, 0, 0]) + bytes(8) + bytes(8) + u32(timescale)
    else:
        payload = bytes([0, 0, 0, 0]) + bytes(4) + bytes(4) + u32(timescale)
    return box(b"mdhd", payload)


def _video_trak(track_id: int, timescale: int, codec_config: bytes = b"") -> bytes:
    mdia_payload = _mdhd(timescale) + _hdlr(b"vide")
    trak_payload = _tkhd(track_id) + box(b"mdia", mdia_payload) + codec_config
    return box(b"trak", trak_payload)


def _audio_trak(track_id: int) -> bytes:
    mdia_payload = _mdhd(44100) + _hdlr(b"soun")
    trak_payload = _tkhd(track_id) + box(b"mdia", mdia_payload)
    return box(b"trak", trak_payload)


def _init_segment(*traks: bytes) -> bytes:
    ftyp = box(b"ftyp", b"isom" + bytes(12))
    moov_payload = b"".join(traks)
    return ftyp + box(b"moov", moov_payload)


def test_video_track_id_from_moov_picks_video_handler():
    init_segment = _init_segment(_audio_trak(2), _video_trak(1, 90000))
    assert bridge._video_track_id_from_moov(init_segment) == 1


def test_video_timescale_from_moov():
    init_segment = _init_segment(_video_trak(5, 48000))
    assert bridge._video_timescale_from_moov(init_segment) == 48000


def test_video_track_id_from_moov_version1_tkhd():
    # version-1 tkhd/mdhd path, built directly since the helper above only builds version 0
    mdia_payload = _mdhd(90000, version=1) + _hdlr(b"vide")
    trak_payload = _tkhd(42, version=1) + box(b"mdia", mdia_payload)
    init_segment = _init_segment(box(b"trak", trak_payload))
    assert bridge._video_track_id_from_moov(init_segment) == 42
    assert bridge._video_timescale_from_moov(init_segment) == 90000


def test_video_track_id_from_moov_no_video_track():
    init_segment = _init_segment(_audio_trak(2))
    assert bridge._video_track_id_from_moov(init_segment) is None


def test_video_track_id_from_moov_malformed_input_returns_none():
    assert bridge._video_track_id_from_moov(b"not a moov at all") is None
    assert bridge._video_timescale_from_moov(b"") is None


# --- Codec-string builders (H.264/H.265/AV1) -----------------------------
# H.264 is the only codec ever actually seen on hardware (ffmpeg's
# `-c:v copy` just passes through whatever the SRT source sends) — the
# HEVC/AV1 fixtures below exercise the bit-layout math against the specs
# directly, not a real capture. See _hevc_codec_string()/_av1_codec_string()'s
# own comments.


def _hvcc_payload(
    profile_space=0, tier_flag=0, profile_idc=1,
    compat_flags=0x00000001, constraint_bytes=bytes([0xB0, 0, 0, 0, 0, 0]), level_idc=93,
):
    byte1 = (profile_space << 6) | (tier_flag << 5) | profile_idc
    return bytes([1, byte1]) + compat_flags.to_bytes(4, "big") + constraint_bytes + bytes([level_idc])


def test_avc1_codec_string():
    payload = box(b"avcC", bytes([1, 0x64, 0x00, 0x1F]))
    assert bridge._avc1_codec_string(payload) == "avc1.64001f"


def test_reverse_bits32():
    assert bridge._reverse_bits32(0x00000001) == 0x80000000
    assert bridge._reverse_bits32(0x80000000) == 0x00000001
    assert bridge._reverse_bits32(0) == 0


def test_hevc_codec_string():
    payload = box(b"hvcC", _hvcc_payload())
    assert bridge._hevc_codec_string(payload, "hev1") == "hev1.1.80000000.L93.B0"


def test_hevc_codec_string_high_tier_and_profile_space():
    # profile_space=1 ('A'), tier_flag=1 ('H'), all-zero constraint flags
    # (the whole field is dropped, per RFC 6381's own formatting rule).
    payload = box(
        b"hvcC",
        _hvcc_payload(profile_space=1, tier_flag=1, profile_idc=2, constraint_bytes=bytes(6), level_idc=120),
    )
    assert bridge._hevc_codec_string(payload, "hvc1") == "hvc1.A2.80000000.H120"


def test_video_codec_string_dispatches_hevc_by_sample_entry_tag():
    hvc1_trak = box(b"hvc1", box(b"hvcC", _hvcc_payload()))
    hev1_trak = box(b"hvcC", _hvcc_payload())  # no explicit sample-entry tag -> defaults to hev1
    assert bridge._video_codec_string(hvc1_trak) == "hvc1.1.80000000.L93.B0"
    assert bridge._video_codec_string(hev1_trak) == "hev1.1.80000000.L93.B0"


def test_av1_codec_string_8bit_main_tier():
    payload = box(b"av1C", bytes([0x81, 0x04, 0x00, 0x00]))  # profile=0, level=4, tier=main, 8-bit
    assert bridge._av1_codec_string(payload) == "av01.0.04M.08"


def test_av1_codec_string_high_tier_12bit():
    byte1 = (2 << 5) | 8  # seq_profile=2, seq_level_idx_0=8
    byte2 = (1 << 7) | (1 << 6) | (1 << 5)  # seq_tier_0=1 (High), high_bitdepth=1, twelve_bit=1
    payload = box(b"av1C", bytes([0x81, byte1, byte2, 0x00]))
    assert bridge._av1_codec_string(payload) == "av01.2.08H.12"


def test_video_codec_string_dispatches_av1():
    trak_payload = box(b"av01", box(b"av1C", bytes([0x81, 0x04, 0x00, 0x00])))
    assert bridge._video_codec_string(trak_payload) == "av01.0.04M.08"


def test_video_codec_string_defaults_to_avc1():
    trak_payload = box(b"avcC", bytes([1, 0x4D, 0x00, 0x28]))
    assert bridge._video_codec_string(trak_payload) == "avc1.4d0028"


def test_mime_codec_from_moov_h264():
    codec_config = box(b"avcC", bytes([1, 0x64, 0x00, 0x1F]))
    init_segment = _init_segment(_video_trak(1, 90000, codec_config))
    assert bridge._mime_codec_from_moov(init_segment) == 'video/mp4; codecs="avc1.64001f"'


def test_mime_codec_from_moov_hevc_with_audio():
    codec_config = box(b"hvc1", box(b"hvcC", _hvcc_payload()))
    init_segment = _init_segment(_video_trak(1, 90000, codec_config), box(b"mp4a", b"\x00"))
    assert (
        bridge._mime_codec_from_moov(init_segment)
        == 'video/mp4; codecs="hvc1.1.80000000.L93.B0,mp4a.40.2"'
    )


def test_mime_codec_from_moov_av1():
    codec_config = box(b"av01", box(b"av1C", bytes([0x81, 0x04, 0x00, 0x00])))
    init_segment = _init_segment(_video_trak(1, 90000, codec_config))
    assert bridge._mime_codec_from_moov(init_segment) == 'video/mp4; codecs="av01.0.04M.08"'


def test_mime_codec_from_moov_no_video_trak():
    init_segment = _init_segment(_audio_trak(2))
    assert bridge._mime_codec_from_moov(init_segment) is None


# --- _parse_fragment_keyframe (moof/traf orchestration) ------------------


def _tfhd_box(track_id: int, default_sample_flags: int | None = None) -> bytes:
    tf_flags = 0x000020 if default_sample_flags is not None else 0
    payload = u32(tf_flags) + u32(track_id)
    if default_sample_flags is not None:
        payload += u32(default_sample_flags)
    return box(b"tfhd", payload)


def _tfdt_box(base_media_decode_time: int) -> bytes:
    return box(b"tfdt", bytes([0, 0, 0, 0]) + u32(base_media_decode_time))


def _trun_box(sample_durations: list[int], sync_index: int | None) -> bytes:
    trun_flags = 0x000100 | 0x000400  # duration + per-sample flags
    payload = u32(trun_flags) + u32(len(sample_durations))
    for i, duration in enumerate(sample_durations):
        flags = SYNC_FLAGS if i == sync_index else NON_SYNC_FLAGS
        payload += u32(duration) + u32(flags)
    return box(b"trun", payload)


def test_parse_fragment_keyframe_finds_video_track_and_skips_audio():
    audio_traf = box(b"traf", _tfhd_box(track_id=2))
    video_traf = box(
        b"traf",
        _tfhd_box(track_id=1) + _tfdt_box(48000) + _trun_box([1000, 1000, 1000], sync_index=0),
    )
    moof = box(b"moof", audio_traf + video_traf)
    fragment = moof + box(b"mdat", bytes(4))

    pts = bridge._parse_fragment_keyframe(fragment, video_track_id=1, video_timescale=48000)
    assert pts == 1.0  # 48000 / 48000


def test_parse_fragment_keyframe_offsets_by_prior_sample_durations():
    video_traf = box(
        b"traf",
        _tfhd_box(track_id=1) + _tfdt_box(0) + _trun_box([1000, 1000, 500], sync_index=2),
    )
    fragment = box(b"moof", video_traf) + box(b"mdat", bytes(4))

    pts = bridge._parse_fragment_keyframe(fragment, video_track_id=1, video_timescale=1000)
    assert pts == 2.0  # (0 + 1000 + 1000) / 1000


def test_parse_fragment_keyframe_no_keyframe_in_fragment():
    video_traf = box(
        b"traf",
        _tfhd_box(track_id=1) + _tfdt_box(0) + _trun_box([1000, 1000], sync_index=None),
    )
    fragment = box(b"moof", video_traf) + box(b"mdat", bytes(4))

    assert bridge._parse_fragment_keyframe(fragment, video_track_id=1, video_timescale=1000) is None


def test_parse_fragment_keyframe_no_video_track_info_yet():
    fragment = box(b"moof", b"") + box(b"mdat", bytes(4))
    assert bridge._parse_fragment_keyframe(fragment, None, None) is None


def test_parse_fragment_keyframe_malformed_bytes_returns_none_not_raise():
    assert bridge._parse_fragment_keyframe(b"garbage", video_track_id=1, video_timescale=1000) is None
