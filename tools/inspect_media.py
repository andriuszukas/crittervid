"""Inspect .media files: list their records and summarise video and audio timing.

A .media file is a sequence of records, each a 24-byte little-endian header
(type, payload length, a millisecond value, a zero field, a timestamp in
microseconds) followed by its payload. See docs/decisions/0001-media-format.md.

Read-only. Usage:
    python tools/inspect_media.py FILE_OR_EVENT_FOLDER [...]
"""
import collections
import sys
from pathlib import Path

# The format itself lives in media_format.py at the repository root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from media_format import MediaFormatError, read_records  # noqa: E402

TYPE_NAMES = {0: 'video frame', 1: 'video keyframe', 3: 'audio'}


def summarise(paths):
    """Timing summary of one or more .media files, read in order as one stream."""
    times = collections.defaultdict(list)
    sizes = collections.defaultdict(int)
    for path in paths:
        for rtype, length, t_us, _ in read_records(Path(path).read_bytes()):
            times[rtype].append(t_us)
            sizes[rtype] += length
    video = sorted(times[0] + times[1])
    audio = times[3]
    summary = {'records': {TYPE_NAMES.get(t, f'type {t}'): len(v) for t, v in sorted(times.items())}}
    if len(video) > 1:
        span = (video[-1] - video[0]) / 1e6
        summary['video_span_s'] = round(span, 3)
        summary['video_fps'] = round((len(video) - 1) / span, 2)
        summary['video_gaps_ms'] = dict(collections.Counter(
            round((b - a) / 1000) for a, b in zip(video, video[1:])).most_common(3))
    if len(audio) > 1:
        span = (audio[-1] - audio[0]) / 1e6
        summary['audio_span_s'] = round(span, 3)
        summary['audio_bytes_per_s'] = round(sizes[3] / (span + (audio[1] - audio[0]) / 1e6))
    return summary


def main(argv):
    if not argv:
        print(__doc__.strip())
        return 2
    for arg in argv:
        p = Path(arg)
        files = sorted(p.glob('*.media')) if p.is_dir() else [p]
        try:
            summary = summarise(files)
        except MediaFormatError as e:
            print(f'{arg}: not a valid .media record stream: {e}')
            continue
        print(f'{arg} ({len(files)} file{"s" if len(files) != 1 else ""})')
        for key, value in summary.items():
            print(f'  {key}: {value}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
