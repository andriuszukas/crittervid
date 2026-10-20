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
import collections
import struct

HEADER = struct.Struct('<IIIIQ')
VIDEO_FRAME, VIDEO_KEYFRAME, AUDIO = 0, 1, 3

AUDIO_SAMPLE_RATE = 8000
AUDIO_BYTES_PER_SECOND = AUDIO_SAMPLE_RATE * 2   # 16-bit mono
# Audio records whose timestamp is within this of where the previous audio ended
# are treated as continuous: the cameras' audio timestamps jitter by up to ~16 ms
# while the samples themselves run on without a break
AUDIO_JITTER_US = 20_000


# H.264 NAL unit types: a slice of an IDR picture (one that needs no earlier frame),
# and the sequence and picture parameter sets a decoder needs before any slice
NAL_IDR, NAL_SPS, NAL_PPS = 5, 7, 8


class MediaFormatError(ValueError):
    """The data isn't a .media record stream."""


Streams = collections.namedtuple('Streams', 'video audio trailing_bytes')


def read_records(data, allow_truncated=False):
    """Yield (type, payload_length, timestamp_us, payload) for each record.

    Raises MediaFormatError unless the data consists of whole records; with
    allow_truncated, an incomplete final record (as left by power loss) is
    ignored instead."""
    pos = 0
    while pos < len(data):
        if pos + HEADER.size > len(data):
            if allow_truncated:
                return
            raise MediaFormatError(f'{len(data) - pos} trailing bytes at offset {pos}')
        rtype, length, _ms, _zero, t_us = HEADER.unpack_from(data, pos)
        start = pos + HEADER.size
        if start + length > len(data):
            if allow_truncated:
                return
            raise MediaFormatError(f'record at offset {pos} runs past the end of the data')
        yield rtype, length, t_us, data[start:start + length]
        pos = start + length


def split_streams(data):
    """The video frames [(timestamp_us, payload, is_keyframe)] and audio records
    [(timestamp_us, payload)] of a .media file, in file order, plus the number of
    bytes of an incomplete final record that were ignored.

    Raises MediaFormatError if there's no complete record or no video record."""
    video, audio, used = [], [], 0
    for rtype, length, t_us, payload in read_records(data, allow_truncated=True):
        used += HEADER.size + length
        if rtype in (VIDEO_FRAME, VIDEO_KEYFRAME):
            video.append((t_us, payload, rtype == VIDEO_KEYFRAME))
        elif rtype == AUDIO:
            audio.append((t_us, payload))
    if not used:
        raise MediaFormatError('no complete record')
    if not video:
        raise MediaFormatError('no video records')
    return Streams(video, audio, len(data) - used)


def nal_types(payload):
    """The NAL unit types in an Annex B frame."""
    return {unit[0] & 0x1F for unit in payload.split(b'\x00\x00\x01')[1:] if unit}


def decodable_start(video):
    """The index of the first video frame a decoder can start from (an IDR picture
    with its parameter sets, as the cameras store each keyframe), or None if
    there's none.

    A frame without them holds only slices referring to an earlier picture the
    card doesn't have, so it can't be decoded."""
    for i, (_, payload, _) in enumerate(video):
        if {NAL_IDR, NAL_SPS, NAL_PPS} <= nal_types(payload):
            return i
    return None


def from_parameter_sets(payload):
    """A keyframe's data from its first SPS on.

    The first keyframe of an event can carry slices of earlier pictures in front
    of its parameter sets, left over from before the recording started. They
    can't be decoded, and FFmpeg takes them for frames of their own that share
    the keyframe's timestamp, which shifts the keyframe and the video's start,
    more so when a long pause follows (#77)."""
    pos = 0
    while (found := payload.find(b'\x00\x00\x01', pos)) >= 0:
        if found + 3 < len(payload) and payload[found + 3] & 0x1F == NAL_SPS:
            # Keep a 4-byte start code whole
            return payload[found - 1 if found and payload[found - 1] == 0 else found:]
        pos = found + 3
    return payload


def _audio_offset(t_us, start_us):
    """Byte position in the PCM stream for a time, rounded down to a whole sample."""
    pos = (t_us - start_us) * AUDIO_BYTES_PER_SECOND // 1_000_000
    return pos - pos % 2


def audio_pcm(audio, start_us, end_us):
    """The audio as one PCM stream covering exactly start_us (the first video
    frame) to end_us, so it lines up with the video by itself.

    Each record goes where its timestamp says. Small differences (jitter) are
    absorbed by appending as is; later records leave silence before them; records
    that overlap audio already placed (the cameras' bursts of silent records 20 ms
    apart, duplicates, out-of-order records) only add their part beyond it, except
    that real sound replaces silence. Audio outside start_us..end_us is dropped,
    and missing audio is silence."""
    total = _audio_offset(end_us, start_us)
    pcm = bytearray()
    jitter = _audio_offset(start_us + AUDIO_JITTER_US, start_us)
    for t_us, payload in audio:
        payload = payload[:len(payload) - len(payload) % 2]
        pos = _audio_offset(t_us, start_us)
        if pos >= total or pos + len(payload) <= 0:
            continue
        if pos < 0:
            payload, pos = payload[-pos:], 0
        if abs(pos - len(pcm)) <= jitter:
            pcm += payload          # continuous with what came before
            continue
        if pos > len(pcm):
            pcm += bytes(pos - len(pcm))
            pcm += payload
            continue
        # Overlap: keep audio already placed, but let real sound replace silence
        overlap_end = min(len(pcm), pos + len(payload))
        existing = pcm[pos:overlap_end]
        new = payload[:overlap_end - pos]
        if not any(existing) and any(new):
            pcm[pos:overlap_end] = new
        pcm += payload[overlap_end - pos:]
    if len(pcm) < total:
        pcm += bytes(total - len(pcm))
    return bytes(pcm[:total])


# --- MPEG-TS with one H.264 stream -------------------------------------------

TS_PACKET = 188
PMT_PID, VIDEO_PID = 0x1000, 0x100
PTS_START = 90_000   # start at 1 s, so the clock reference can sit just before it
PTS_MASK = (1 << 33) - 1


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
        body += (((pcr & PTS_MASK) << 15) | (0x3F << 9)).to_bytes(6, 'big')
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

    Returns the TS bytes; the first frame is at PTS_START (1 s). Raises
    MediaFormatError if a frame isn't later than the one before it."""
    t0 = video[0][0]
    out, cc, table_cc, previous = [], 0, 0, None
    for t_us, payload, keyframe in video:
        if previous is not None and t_us <= previous:
            raise MediaFormatError(f'video timestamp {t_us} does not follow {previous}')
        previous = t_us
        if keyframe or not out:
            out.append(_tables(table_cc))
            table_cc = (table_cc + 1) % 16
        # Microseconds to the 90 kHz clock, which wraps at 33 bits (about 26.5 h)
        pts = (PTS_START + (t_us - t0) * 9 // 100) & PTS_MASK
        # PES header: no length (allowed for video), data aligned to the frame, PTS
        pes = b'\x00\x00\x01\xE0\x00\x00\x84\x80\x05' + _pts_bytes(pts) + payload
        packets, cc = _pes_packets(pes, pts, keyframe, cc)
        out.append(packets)
    return b''.join(out)
