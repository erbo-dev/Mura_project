"""Pure-Python server-side audio duration extraction.

Supports WAV, Ogg (Opus/Vorbis), WebM (EBML), and MP3 without external
`ffprobe` or `ffmpeg` binary dependencies.
"""

from __future__ import annotations

import io
import struct
import wave
from pathlib import Path
from typing import BinaryIO


class AudioDurationError(Exception):
    """Raised when audio duration cannot be parsed or format is invalid."""


def probe_audio_duration(
    source: BinaryIO | bytes | Path | str,
    *,
    content_type: str | None = None,
    filename: str | None = None,
) -> float | None:
    """Extract audio duration in seconds from an audio stream, bytes, or file.

    Returns:
        Duration in seconds rounded to 3 decimal places, or None if the
        stream/file cannot be parsed.
    """
    stream: BinaryIO
    should_close = False

    if isinstance(source, (str, Path)):
        stream = open(source, "rb")
        should_close = True
    elif isinstance(source, bytes):
        stream = io.BytesIO(source)
    else:
        stream = source

    try:
        initial_pos = stream.tell() if stream.seekable() else 0
    except Exception:
        initial_pos = 0

    try:
        # Read header prefix to identify format
        header = stream.read(12)
        if stream.seekable():
            stream.seek(initial_pos)

        # 1. WAV
        if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
            duration = _probe_wav(stream, initial_pos)
            if duration is not None:
                return duration

        # 2. Ogg (Opus / Vorbis)
        if header.startswith(b"OggS"):
            duration = _probe_ogg(stream, initial_pos)
            if duration is not None:
                return duration

        # 3. WebM / Matroska
        if header.startswith(b"\x1a\x45\xdf\xa3"):
            duration = _probe_webm(stream, initial_pos)
            if duration is not None:
                return duration

        # 4. MP3 (ID3v2 or raw MPEG frames)
        is_mp3_header = header.startswith(b"ID3") or (
            len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
        )
        if is_mp3_header:
            duration = _probe_mp3(stream, initial_pos)
            if duration is not None:
                return duration

        # If header wasn't matched above, try format hint from content_type or filename
        hint = (content_type or "") + " " + (filename or "")
        hint = hint.lower()
        if "wav" in hint:
            return _probe_wav(stream, initial_pos)
        if "ogg" in hint or "opus" in hint:
            return _probe_ogg(stream, initial_pos)
        if "webm" in hint:
            return _probe_webm(stream, initial_pos)
        if "mp3" in hint or "mpeg" in hint:
            return _probe_mp3(stream, initial_pos)

        return None
    finally:
        if stream.seekable():
            try:
                stream.seek(initial_pos)
            except (OSError, ValueError):
                pass  # nosec B110
        if should_close:
            stream.close()


def _probe_wav(stream: BinaryIO, initial_pos: int) -> float | None:
    """Extract WAV duration via Python standard library wave module."""
    if stream.seekable():
        stream.seek(initial_pos)
    try:
        with wave.open(stream, "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            if rate > 0:
                return round(frames / float(rate), 3)
    except (wave.Error, OSError, EOFError, struct.error, ValueError):
        pass  # nosec B110
    return None


def _probe_ogg(stream: BinaryIO, initial_pos: int) -> float | None:
    """Extract Ogg (Opus / Vorbis) duration by reading the last page granule position."""
    if not stream.seekable():
        data = stream.read()
        stream = io.BytesIO(data)
        initial_pos = 0

    # Scan stream to find sample rate
    # Opus is standardized to a 48000 Hz reference clock for granule positions
    sample_rate = 48000

    stream.seek(initial_pos)
    first_header = stream.read(64)
    if b"vorbis" in first_header.lower():
        # Vorbis header packet: audio sample rate is at byte 12 (uint32 LE) in identification packet
        stream.seek(initial_pos)
        buf = stream.read(1024)
        v_idx = buf.find(b"\x01vorbis")
        if v_idx != -1 and len(buf) >= v_idx + 15:
            sample_rate = struct.unpack_from("<I", buf, v_idx + 11)[0]
            if sample_rate <= 0:
                sample_rate = 44100

    # Find the last Ogg page to read end granule position
    stream.seek(0, io.SEEK_END)
    file_size = stream.tell()
    search_size = min(file_size, 65536)
    stream.seek(file_size - search_size)
    tail = stream.read(search_size)

    last_granule = -1
    pos = len(tail)
    while True:
        pos = tail.rfind(b"OggS", 0, pos)
        if pos == -1:
            break
        if pos + 14 <= len(tail):
            # Header flags byte 5, Granule position bytes 6..14 (uint64 LE)
            granule = struct.unpack_from("<Q", tail, pos + 6)[0]
            # Valid granule position is >= 0 and not all 1s (0xFFFFFFFFFFFFFFFF)
            if granule != 0xFFFFFFFFFFFFFFFF and granule > 0:
                last_granule = granule
                break
        pos -= 1

    if last_granule > 0 and sample_rate > 0:
        return round(last_granule / float(sample_rate), 3)

    return None


def _probe_webm(stream: BinaryIO, initial_pos: int) -> float | None:
    """Extract WebM / Matroska duration by parsing EBML Info Segment."""
    if not stream.seekable():
        data = stream.read()
        stream = io.BytesIO(data)
        initial_pos = 0

    stream.seek(initial_pos)
    raw = stream.read(65536)
    if len(raw) < 12:
        return None

    def read_vint(buffer: bytes, offset: int) -> tuple[int, int] | None:
        if offset >= len(buffer):
            return None
        first = buffer[offset]
        length = 1
        mask = 0x80
        while length <= 8 and not (first & mask):
            length += 1
            mask >>= 1
        if length > 8 or offset + length > len(buffer):
            return None
        val = first & (mask - 1)
        for i in range(1, length):
            val = (val << 8) | buffer[offset + i]
        return val, length

    # Look for Info element (ID 0x1549A966)
    info_id = b"\x15\x49\xa9\x66"
    info_idx = raw.find(info_id)
    if info_idx == -1:
        return None

    # Inside Info element, look for TimecodeScale (0x2AD7B1) and Duration (0x4489)
    # Search within next 4096 bytes
    info_chunk = raw[info_idx : info_idx + 4096]

    # TimecodeScale default is 1,000,000 ns (1 ms)
    timecode_scale = 1_000_000
    tc_id = b"\x2a\xd7\xb1"
    tc_idx = info_chunk.find(tc_id)
    if tc_idx != -1:
        offset = tc_idx + len(tc_id)
        vint = read_vint(info_chunk, offset)
        if vint:
            size, length = vint
            val_offset = offset + length
            if val_offset + size <= len(info_chunk):
                tc_bytes = info_chunk[val_offset : val_offset + size]
                timecode_scale = int.from_bytes(tc_bytes, "big")

    duration_id = b"\x44\x89"
    dur_idx = info_chunk.find(duration_id)
    if dur_idx != -1:
        offset = dur_idx + len(duration_id)
        vint = read_vint(info_chunk, offset)
        if vint:
            size, length = vint
            val_offset = offset + length
            if size == 4 and val_offset + 4 <= len(info_chunk):
                duration_val = struct.unpack_from(">f", info_chunk, val_offset)[0]
                return round((duration_val * timecode_scale) / 1e9, 3)
            elif size == 8 and val_offset + 8 <= len(info_chunk):
                duration_val = struct.unpack_from(">d", info_chunk, val_offset)[0]
                return round((duration_val * timecode_scale) / 1e9, 3)

    return None


def _probe_mp3(stream: BinaryIO, initial_pos: int) -> float | None:
    """Extract MP3 duration via ID3v2 TLEN or MPEG Xing/CBR header parsing."""
    if not stream.seekable():
        data = stream.read()
        stream = io.BytesIO(data)
        initial_pos = 0

    stream.seek(0, io.SEEK_END)
    file_size = stream.tell() - initial_pos
    stream.seek(initial_pos)

    header = stream.read(10)
    id3_size = 0
    if header.startswith(b"ID3") and len(header) == 10:
        # Synchsafe integer tag size
        tag_size = (
            ((header[6] & 0x7F) << 21)
            | ((header[7] & 0x7F) << 14)
            | ((header[8] & 0x7F) << 7)
            | (header[9] & 0x7F)
        )
        id3_size = 10 + tag_size
        id3_data = stream.read(min(tag_size, 32768))

        # Check for TLEN frame in ID3
        tlen_idx = id3_data.find(b"TLEN")
        if tlen_idx != -1 and tlen_idx + 10 <= len(id3_data):
            frame_size = struct.unpack_from(">I", id3_data, tlen_idx + 4)[0]
            # Strip encoding byte and decode
            body = id3_data[tlen_idx + 10 : tlen_idx + 10 + frame_size]
            try:
                ms_str = body.lstrip(b"\x00\x01\x02\x03").decode("ascii", errors="ignore").strip()
                if ms_str.isdigit():
                    return round(float(ms_str) / 1000.0, 3)
            except (UnicodeDecodeError, ValueError, struct.error):
                pass  # nosec B110

    # Seek past ID3 tag to scan MPEG frames
    stream.seek(initial_pos + id3_size)
    chunk = stream.read(16384)
    if len(chunk) < 4:
        return None

    # Search for MPEG frame sync (11 bits of 1s)
    sample_rates_v1 = [44100, 48000, 32000]
    sample_rates_v2 = [22050, 24000, 16000]
    sample_rates_v25 = [11025, 12000, 8000]
    bitrates_v1_l3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]

    for i in range(len(chunk) - 4):
        b0, b1 = chunk[i], chunk[i + 1]
        if b0 == 0xFF and (b1 & 0xE0) == 0xE0:
            version_id = (b1 >> 3) & 0x03
            layer = (b1 >> 1) & 0x03
            if version_id == 1 or layer == 0:  # reserved
                continue

            b2 = chunk[i + 2]
            bitrate_idx = (b2 >> 4) & 0x0F
            sr_idx = (b2 >> 2) & 0x03
            if bitrate_idx == 0x0F or sr_idx == 0x03:
                continue

            if version_id == 3:  # MPEG-1
                sr = sample_rates_v1[sr_idx]
                bitrate = bitrates_v1_l3[bitrate_idx] * 1000
            elif version_id == 2:  # MPEG-2
                sr = sample_rates_v2[sr_idx]
                bitrate = 64000
            else:  # MPEG-2.5
                sr = sample_rates_v25[sr_idx]
                bitrate = 32000

            channel_mode = (chunk[i + 3] >> 6) & 0x03
            side_info_len = 32 if (version_id == 3 and channel_mode != 3) else 17
            xing_offset = i + 4 + side_info_len

            if xing_offset + 12 <= len(chunk):
                header_magic = chunk[xing_offset : xing_offset + 4]
                if header_magic in (b"Xing", b"Info"):
                    flags = struct.unpack_from(">I", chunk, xing_offset + 4)[0]
                    if flags & 0x01:  # Frames flag present
                        frame_count = struct.unpack_from(">I", chunk, xing_offset + 8)[0]
                        samples_per_frame = 1152 if version_id == 3 else 576
                        if sr > 0:
                            return round((frame_count * samples_per_frame) / float(sr), 3)

            # Fallback CBR estimate
            if bitrate > 0:
                audio_bytes = max(0, file_size - id3_size)
                return round((audio_bytes * 8) / float(bitrate), 3)

    return None
