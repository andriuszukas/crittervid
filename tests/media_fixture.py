"""Write .media files in the cameras' record format, for tests.

Mirrors what was found on real cards (docs/decisions/0001-media-format.md):
records with a 24-byte header and a microsecond timestamp; H.264 frames (the
keyframe record carrying SPS/PPS) 50 or 100 ms apart, about 14.7 fps overall;
640-byte PCM audio records every 40 ms, starting a little after the first video
frame.

Options of write_event reproduce the oddities seen on real cards: bursts of
zero-filled audio, recording gaps, damaged and empty segments, repeated frames
and garbage records, events without audio.

Simplification: the event folder's date is the UTC date of its start. Real cards
use the camera's local date.
"""
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HEADER = struct.Struct('<IIIIQ')
# Gaps between frames, repeating: seven 50 ms and four 100 ms interleaved, the
# 1.75:1 ratio seen on real cards (about 14.7 fps)
FRAME_GAPS_US = (50_000, 100_000, 50_000, 50_000, 100_000, 50_000, 100_000, 50_000, 50_000, 100_000, 50_000)
KEYFRAME_EVERY = 66                 # frames
AUDIO_RECORD_US = 40_000
AUDIO_RECORD_BYTES = 640            # 40 ms of 8 kHz 16-bit mono
BURST_RECORD_US = 20_000            # zero-filled records in a burst are this far apart


def h264_frames(count, size='320x240'):
    """`count` H.264 frames from FFmpeg's test source, as [(annex_b_bytes, is_keyframe)].

    Parameter sets and SEI go into the record of the frame that follows them, as
    the cameras store them with each keyframe."""
    raw = subprocess.run(
        ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'testsrc=size={size}:rate=15',
         '-frames:v', str(count), '-c:v', 'libx264', '-preset', 'ultrafast', '-bf', '0', '-g', str(KEYFRAME_EVERY), '-sc_threshold', '0',
         '-f', 'h264', '-'],
        capture_output=True, check=True,
    ).stdout
    frames, pending = [], []
    for nal in _nal_units(raw):
        if nal[0] & 0x1F in (1, 5):   # a coded slice ends the frame
            frames.append((b''.join(b'\x00\x00\x00\x01' + n for n in pending + [nal]), nal[0] & 0x1F == 5))
            pending = []
        else:
            pending.append(nal)
    assert len(frames) == count, (len(frames), count)
    return frames


def _nal_units(data):
    """Split an Annex B stream into NAL units (without start codes)."""
    starts, pos = [], 0
    while (found := data.find(b'\x00\x00\x01', pos)) >= 0:
        starts.append(found)
        pos = found + 3
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(data)
        if i + 1 < len(starts) and data[end - 1] == 0:
            end -= 1   # the leading zero of a 4-byte start code
        yield data[start + 3:end]


def tone(n_bytes, period=16):
    """A square-wave tone as 16-bit PCM: clearly not silence."""
    samples = [8000 if (i // (period // 2)) % 2 else -8000 for i in range(n_bytes // 2)]
    return struct.pack(f'<{len(samples)}h', *samples)


def marker_audio(t_us, marker_us):
    """640 bytes of audio at t_us that are silent except for a loud tone from
    marker_us for 200 ms, so a test can find that moment in the converted audio."""
    out = []
    for i in range(AUDIO_RECORD_BYTES // 2):
        since = t_us + i * 125 - marker_us   # 125 us per sample
        out.append((8000 if (since // 125 // 8) % 2 else -8000) if 0 <= since < 200_000 else 0)
    return struct.pack(f'<{len(out)}h', *out)


def write_event(dcim, start, seconds, segment_seconds=13, audio_offset_us=38_000, folder_seconds=None,
                frame_size='320x240', *, audio_bursts=(), gap=None, truncate_last_bytes=0, empty_segment=None,
                repeat_first_frames=0, garbage_tail=0, no_audio=False, marker_at_s=None):
    """Write one event folder DCIM/YYYY/MM/DD/<start>_<duration>/<offset>.media.

    `start` is an aware datetime; the folder's date is its UTC date. `seconds` is
    the event's span on the camera's clock, a recording gap included. Returns the
    event folder and the video timestamps (microseconds) of every real frame.

    Options for the oddities of real cards:
    - audio_bursts=[(at_s, n)]: at_s into the event, n zero-filled audio records
      20 ms apart replace the normal audio for n * 20 ms
    - gap=(at_s, gap_s): a recording pause, with no video or audio for gap_s seconds
    - truncate_last_bytes=N: the last segment file loses its last N bytes
    - empty_segment=i: segment i is a 0-byte file
    - repeat_first_frames=N: segment 1 starts with the first N frames of segment 0
      again (and their audio), with their original timestamps
    - garbage_tail=N: N video records with timestamp 0 end the last segment
    - no_audio=True: no audio records at all
    - marker_at_s: all audio is silent except a loud tone for 200 ms from there"""
    t0 = int(start.timestamp()) * 1_000_000
    gap_start = t0 + int(gap[0] * 1_000_000) if gap else None
    gap_end = gap_start + int(gap[1] * 1_000_000) if gap else None
    stamps = [t0]
    while stamps[-1] - t0 < seconds * 1_000_000:
        stamp = stamps[-1] + FRAME_GAPS_US[(len(stamps) - 1) % len(FRAME_GAPS_US)]
        if gap and stamps[-1] < gap_start <= stamp:
            stamp += gap_end - gap_start
        stamps.append(stamp)
    frames = h264_frames(len(stamps), frame_size)
    duration = seconds if folder_seconds is None else folder_seconds
    folder = Path(dcim) / start.strftime('%Y/%m/%d') / f'{int(start.timestamp())}_{duration:04d}'
    folder.mkdir(parents=True)

    # Split into segments, each starting with a keyframe, about segment_seconds long
    segments, current = [], []
    for stamp, (payload, key) in zip(stamps, frames):
        if current and key and stamp - current[0][0] >= segment_seconds * 1_000_000:
            segments.append(current)
            current = []
        current.append((stamp, payload, key))
    segments.append(current)

    audio = [] if no_audio else audio_records(t0, stamps[-1], audio_offset_us, audio_bursts, gap_start, gap_end,
                                               None if marker_at_s is None else t0 + int(marker_at_s * 1_000_000))
    records = []   # per segment: [(timestamp, record type, payload)] in time order
    for i, seg in enumerate(segments):
        seg_end = seg[-1][0] if i < len(segments) - 1 else float('inf')
        mine = [(stamp, 1 if key else 0, payload) for stamp, payload, key in seg]
        while audio and audio[0][0] <= seg_end:
            mine.append((audio[0][0], 3, audio.pop(0)[1]))
        mine.sort(key=lambda r: r[0])
        records.append(mine)
    names = [f'{(seg[0][0] - t0) // 1_000_000:04d}.media' for seg in segments]

    if repeat_first_frames:
        assert len(segments) > 1, 'repeat_first_frames needs a second segment'
        frame_times = [r[0] for r in records[0] if r[1] in (0, 1)]
        last = frame_times[repeat_first_frames - 1]
        records[1] = [r for r in records[0] if r[0] <= last] + records[1]
    records[-1] += [(0, 0, bytes(8))] * garbage_tail

    for name, recs in zip(names, records):
        with open(folder / name, 'wb') as f:
            for stamp, rtype, payload in recs:
                f.write(HEADER.pack(rtype, len(payload), (stamp // 1000) & 0xFFFFFFFF, 0, stamp) + payload)
    if empty_segment is not None:
        (folder / names[empty_segment]).write_bytes(b'')
    if truncate_last_bytes:
        last_file = folder / names[-1]
        last_file.write_bytes(last_file.read_bytes()[:-truncate_last_bytes])
    return folder, stamps


def audio_records(t0, end_us, offset_us, bursts, gap_start, gap_end, marker_us):
    """Audio records [(timestamp, payload)] from t0 + offset_us up to end_us."""
    pending = sorted((t0 + int(at * 1_000_000), n) for at, n in bursts)
    out, t = [], t0 + offset_us
    while t <= end_us:
        if gap_start is not None and gap_start <= t < gap_end:
            t += AUDIO_RECORD_US
        elif pending and t >= pending[0][0]:
            n = pending.pop(0)[1]
            out += [(t + i * BURST_RECORD_US, bytes(AUDIO_RECORD_BYTES)) for i in range(n)]
            t += n * BURST_RECORD_US
        else:
            out.append((t, tone(AUDIO_RECORD_BYTES) if marker_us is None else marker_audio(t, marker_us)))
            t += AUDIO_RECORD_US
    return out


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)
