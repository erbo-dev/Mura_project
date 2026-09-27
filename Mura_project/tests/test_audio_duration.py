from __future__ import annotations

import io
import struct
import wave

from mura.audio_duration import probe_audio_duration


def _create_wav_bytes(duration_seconds: float, sample_rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        num_frames = int(duration_seconds * sample_rate)
        # 16-bit PCM silence
        w.writeframes(b"\x00\x00" * num_frames)
    return buffer.getvalue()


def test_probe_wav_duration_exact() -> None:
    wav_bytes = _create_wav_bytes(12.5, sample_rate=16000)
    dur = probe_audio_duration(wav_bytes)
    assert dur is not None
    assert abs(dur - 12.5) < 0.01


def test_probe_wav_duration_short() -> None:
    wav_bytes = _create_wav_bytes(0.5, sample_rate=8000)
    dur = probe_audio_duration(io.BytesIO(wav_bytes))
    assert dur is not None
    assert abs(dur - 0.5) < 0.01


def test_probe_ogg_opus_duration() -> None:
    # Build a minimal synthetic Ogg page structure with granule position
    # Page 1 (BOS): granule 0
    # Page 2 (EOS): granule 48000 * 5 = 240000 (5 seconds at 48000Hz)
    bos_page = (
        b"OggS"
        + b"\x00"  # version
        + b"\x02"  # header type (BOS)
        + struct.pack("<Q", 0)  # granule pos 0
        + b"\x01\x00\x00\x00"  # serial
        + b"\x00\x00\x00\x00"  # seq
        + b"\x00\x00\x00\x00"  # crc
        + b"\x01\x08"  # 1 segment of 8 bytes
        + b"OpusHead"  # payload
    )
    eos_page = (
        b"OggS"
        + b"\x00"
        + b"\x04"  # EOS
        + struct.pack("<Q", 48000 * 5)  # granule pos for 5.0 seconds
        + b"\x01\x00\x00\x00"
        + b"\x01\x00\x00\x00"
        + b"\x00\x00\x00\x00"
        + b"\x01\x04"
        + b"\x00\x00\x00\x00"
    )
    ogg_data = bos_page + eos_page
    dur = probe_audio_duration(ogg_data, content_type="audio/ogg")
    assert dur is not None
    assert abs(dur - 5.0) < 0.01


def test_probe_webm_duration() -> None:
    # Minimal EBML + Segment + Info chunk
    # EBML header ID: 0x1A45DFA3, length 0
    ebml = b"\x1a\x45\xdf\xa3\x80"
    # Info element ID: 0x1549A966
    # Inside Info:
    # TimecodeScale (0x2AD7B1, size 3, val 1000000 ns = 1 ms) -> \x2a\xd7\xb1\x83\x0f\x42\x40
    # Duration (0x4489, size 4, float32 10000.0 ms = 10.0 s) -> \x44\x89\x84 + float32(10000.0)
    tc_elem = b"\x2a\xd7\xb1\x83\x0f\x42\x40"
    dur_elem = b"\x44\x89\x84" + struct.pack(">f", 10000.0)
    info_body = tc_elem + dur_elem
    info_elem = b"\x15\x49\xa9\x66" + bytes([0x80 | len(info_body)]) + info_body
    webm_data = ebml + info_elem

    dur = probe_audio_duration(webm_data, content_type="audio/webm")
    assert dur is not None
    assert abs(dur - 10.0) < 0.01


def test_probe_mp3_tlen_duration() -> None:
    # ID3v2 header: b"ID3" + version (3, 0) + flags (0) + synchsafe size
    # TLEN frame: b"TLEN" + size (4 bytes big-endian) + flags (2 bytes) + body (encoding + "3200")
    tlen_body = b"\x00" + b"3200"  # 3.2 seconds
    tlen_frame = b"TLEN" + struct.pack(">IH", len(tlen_body), 0) + tlen_body
    tag_size = len(tlen_frame)
    # synchsafe encode tag_size
    s0 = (tag_size >> 21) & 0x7F
    s1 = (tag_size >> 14) & 0x7F
    s2 = (tag_size >> 7) & 0x7F
    s3 = tag_size & 0x7F
    id3_header = b"ID3\x03\x00\x00" + bytes([s0, s1, s2, s3])
    mp3_data = id3_header + tlen_frame + b"\xff\xfb\x90\x64" + b"\x00" * 100

    dur = probe_audio_duration(mp3_data, content_type="audio/mp3")
    assert dur is not None
    assert abs(dur - 3.2) < 0.01


def test_probe_mp3_cbr_fallback() -> None:
    # 0xFF, 0xFB (MPEG-1 Layer 3), 0x90 (128kbps, 44100Hz), 0x64
    frame_header = b"\xff\xfb\x90\x64"
    # 16000 bytes at 128 kbps = 16000 * 8 / 128000 = 1.0 second
    cbr_data = frame_header + b"\x00" * 15996
    dur = probe_audio_duration(cbr_data, content_type="audio/mp3")
    assert dur is not None
    assert abs(dur - 1.0) < 0.01


def test_probe_unrecognized_returns_none() -> None:
    random_bytes = b"NOT_AN_AUDIO_FILE_JUST_SOME_RANDOM_TEXT"
    dur = probe_audio_duration(random_bytes)
    assert dur is None
