"""The .media format written by the cameras, and turning it into streams FFmpeg can
put into an MP4 without re-encoding the video.

A .media file is a sequence of records: a 24-byte little-endian header (type,
payload length, a millisecond value, a zero field, a timestamp in microseconds)
followed by the payload. Types: 0 video frame and 1 video keyframe (H.264, Annex B,
no B-frames), 3 audio (PCM signed 16-bit, 8 kHz, mono, 40 ms per record).
See docs/decisions/0001-media-format.md.

FFmpeg reads the H.264 as a raw stream and ignores these timestamps, so the video
is wrapped in MPEG-TS here, where every frame carries its own timestamp.
"""
import struct

HEADER = struct.Struct('<IIIIQ')
VIDEO_FRAME, VIDEO_KEYFRAME, AUDIO = 0, 1, 3

AUDIO_SAMPLE_RATE = 8000
AUDIO_BYTES_PER_SECOND = AUDIO_SAMPLE_RATE * 2   # 16-bit mono
AUDIO_RECORD_US = 40_000
# Fill gaps in the audio longer than this with silence, so later audio stays in
# sync; shorter differences are timing jitter that evens out
AUDIO_GAP_FILL_US = 60_000


class MediaFormatError(ValueError):
    """The data isn't a .media record stream."""


def read_records(data):
    """Yield (type, payload_length, timestamp_us, payload) for each record.

    Raises MediaFormatError unless the data consists exactly of whole records."""
    pos = 0
    while pos < len(data):
        if pos + HEADER.size > len(data):
            raise MediaFormatError(f'{len(data) - pos} trailing bytes at offset {pos}')
        rtype, length, _ms, _zero, t_us = HEADER.unpack_from(data, pos)
        start = pos + HEADER.size
        if start + length > len(data):
            raise MediaFormatError(f'record at offset {pos} runs past the end of the data')
        yield rtype, length, t_us, data[start:start + length]
        pos = start + length


def split_streams(data):
    """The video frames [(timestamp_us, payload, is_keyframe)] and audio records
    [(timestamp_us, payload)] of a .media file, in file order."""
    if not data:
        raise MediaFormatError('empty file')
    video, audio = [], []
    for rtype, _, t_us, payload in read_records(data):
        if rtype in (VIDEO_FRAME, VIDEO_KEYFRAME):
            video.append((t_us, payload, rtype == VIDEO_KEYFRAME))
        elif rtype == AUDIO:
            audio.append((t_us, payload))
    if not video:
        raise MediaFormatError('no video records')
    return video, audio


def audio_pcm(audio, start_us):
    """The audio as one continuous PCM stream that starts at start_us (the first
    video frame), so it lines up with the video without relying on container
    offsets. Silence is added before audio that starts later, audio from before
    start_us is dropped, and gaps longer than AUDIO_GAP_FILL_US are filled with
    silence."""
    out = []
    lead = (audio[0][0] - start_us) * AUDIO_BYTES_PER_SECOND // 1_000_000 if audio else 0
    if lead > 0:
        out.append(b'\x00' * (lead - lead % 2))
    expected = None
    for t_us, payload in audio:
        if expected is not None and t_us - expected > AUDIO_GAP_FILL_US - AUDIO_RECORD_US:
            silence = (t_us - expected) * AUDIO_BYTES_PER_SECOND // 1_000_000
            out.append(b'\x00' * (silence - silence % 2))
        out.append(payload)
        if expected is None or t_us > expected - AUDIO_RECORD_US:
            # A record far out of order (rare) doesn't move the expected time back
            expected = t_us + len(payload) * 1_000_000 // AUDIO_BYTES_PER_SECOND
    pcm = b''.join(out)
    if lead < 0:
        pcm = pcm[-lead - (-lead) % 2:]
    return pcm


# --- MPEG-TS with one H.264 stream -------------------------------------------

TS_PACKET = 188
PMT_PID, VIDEO_PID = 0x1000, 0x100
PTS_START = 90_000   # start at 1 s, so the clock reference can sit just before it


def _crc32_mpeg2(data):
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else crc << 1
            crc &= 0xFFFFFFFF
    return crc


def _section(table_id, body):
    """A PSI section: header, body and CRC."""
    length = len(body) + 4
    data = bytes([table_id, 0xB0 | (length >> 8), length & 0xFF]) + body
    return data + _crc32_mpeg2(data).to_bytes(4, 'big')


def _psi_packet(pid, section, cc):
    payload = b'\x00' + section   # pointer field
    header = bytes([0x47, 0x40 | (pid >> 8), pid & 0xFF, 0x10 | cc])
    return header + payload + b'\xFF' * (TS_PACKET - 4 - len(payload))


def _tables(cc):
    pat = _section(0x00, b'\x00\x01\xC1\x00\x00' + b'\x00\x01' + (0xE000 | PMT_PID).to_bytes(2, 'big'))
    pmt = _section(0x02, b'\x00\x01\xC1\x00\x00'
                   + (0xE000 | VIDEO_PID).to_bytes(2, 'big')    # clock reference PID
                   + b'\xF0\x00'                                # no program info
                   + b'\x1B' + (0xE000 | VIDEO_PID).to_bytes(2, 'big') + b'\xF0\x00')  # H.264 stream
    return _psi_packet(0, pat, cc) + _psi_packet(PMT_PID, pmt, cc)


def _pts_bytes(pts):
    return bytes([0x21 | ((pts >> 29) & 0x0E), (pts >> 22) & 0xFF, ((pts >> 14) & 0xFE) | 1,
                  (pts >> 7) & 0xFF, ((pts << 1) & 0xFE) | 1])


def _adaptation_field(space, pcr=None, random_access=False):
    """An adaptation field exactly `space` bytes long (space >= 1)."""
    if space == 1 and pcr is None and not random_access:
        return b'\x00'
    body = bytes([(0x40 if random_access else 0) | (0x10 if pcr is not None else 0)])
    if pcr is not None:
        body += ((pcr << 15) | (0x3F << 9)).to_bytes(6, 'big')
    stuffing = space - 1 - len(body)
    if stuffing < 0:
        raise ValueError('adaptation field does not fit')
    return bytes([space - 1]) + body + b'\xFF' * stuffing


def _pes_packets(pes, pts, keyframe, cc):
    """Split one PES packet into TS packets; returns (bytes, next continuity counter)."""
    out = []
    pos, first = 0, True
    while pos < len(pes):
        # The first packet of each frame carries a clock reference 50 ms before it,
        # which takes an 8-byte adaptation field
        pcr = pts - 4500 if first else None
        chunk = pes[pos:pos + TS_PACKET - 4 - (8 if first else 0)]
        pos += len(chunk)
        space = TS_PACKET - 4 - len(chunk)
        af = _adaptation_field(space, pcr, first and keyframe) if space else b''
        control = 0x30 if af else 0x10
        header = bytes([0x47, (0x40 if first else 0) | (VIDEO_PID >> 8), VIDEO_PID & 0xFF, control | cc])
        out.append(header + af + chunk)
        cc = (cc + 1) % 16
        first = False
    return b''.join(out), cc


def video_ts(video):
    """An MPEG-TS stream of the video frames, each with its own timestamp.

    Returns the TS bytes; the first frame is at PTS_START (1 s)."""
    t0 = video[0][0]
    out, cc, table_cc = [], 0, 0
    for t_us, payload, keyframe in video:
        if keyframe or not out:
            out.append(_tables(table_cc))
            table_cc = (table_cc + 1) % 16
        pts = PTS_START + (t_us - t0) * 9 // 100   # microseconds -> 90 kHz
        pes = b'\x00\x00\x01\xE0\x00\x00\x80\x80\x05' + _pts_bytes(pts) + payload
        packets, cc = _pes_packets(pes, pts, keyframe, cc)
        out.append(packets)
    return b''.join(out)
