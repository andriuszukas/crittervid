"""Write .media files in the cameras' record format, for tests.

Mirrors what was found on real cards (docs/decisions/0001-media-format.md):
records with a 24-byte header and a microsecond timestamp; H.264 frames (the
keyframe record carrying SPS/PPS) 50 or 100 ms apart; 640-byte PCM audio records
every 40 ms, starting a little after the first video frame.
"""
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HEADER = struct.Struct('<IIIIQ')
FRAME_GAPS_US = (50_000, 100_000)   # alternating, as on real cards (~13.3 fps)
AUDIO_RECORD_US = 40_000
AUDIO_RECORD_BYTES = 640            # 40 ms of 8 kHz 16-bit mono


def h264_frames(count, size='320x240'):
    """`count` H.264 frames from FFmpeg's test source, as [(annex_b_bytes, is_keyframe)].

    Parameter sets and SEI go into the record of the frame that follows them, as
    the cameras store them with each keyframe."""
    raw = subprocess.run(
        ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'testsrc=size={size}:rate=15',
         '-frames:v', str(count), '-c:v', 'libx264', '-preset', 'ultrafast', '-bf', '0', '-g', '45',
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


def write_event(dcim, start, seconds, segment_seconds=13, audio_offset_us=38_000, folder_seconds=None,
                frame_size='320x240'):
    """Write one event folder DCIM/YYYY/MM/DD/<start>_<duration>/<offset>.media.

    `start` is an aware datetime; the folder's date is its UTC date. Returns the
    event folder and the video timestamps (microseconds) of every frame."""
    t0 = int(start.timestamp()) * 1_000_000
    stamps = [t0]
    while stamps[-1] - t0 < seconds * 1_000_000:
        stamps.append(stamps[-1] + FRAME_GAPS_US[len(stamps) % 2])
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

    audio_t = t0 + audio_offset_us
    for seg in segments:
        records = [(stamp, 1 if key else 0, payload) for stamp, payload, key in seg]
        seg_end = seg[-1][0]
        while audio_t <= seg_end:
            records.append((audio_t, 3, tone(AUDIO_RECORD_BYTES)))
            audio_t += AUDIO_RECORD_US
        records.sort(key=lambda r: r[0])
        offset = (seg[0][0] - t0) // 1_000_000
        with open(folder / f'{offset:04d}.media', 'wb') as f:
            for stamp, rtype, payload in records:
                f.write(HEADER.pack(rtype, len(payload), (stamp // 1000) & 0xFFFFFFFF, 0, stamp) + payload)
    return folder, stamps


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)
