"""
Stand-in for ffprobe used by the tests, reading the stand-in files that
fake_ffmpeg.py writes (b'OK' followed by the input's content).

Each file is given a video and an audio stream starting at 0. Its length in
seconds is the number of clips in it: an output's content is b'OK' + its input,
so a converted file contains as many b'OK' as clips, and a merged file (b'OK' +
its inputs) one more. A file containing b'NOAUDIO' has no audio stream. A file
that doesn't start with b'OK' is unreadable (exit 1).
"""
import json
import sys
from pathlib import Path


def main():
    path = Path(sys.argv[-1])
    try:
        data = path.read_bytes()
    except OSError:
        sys.exit(1)
    if not data.startswith(b'OK'):
        print(f'{path}: Invalid data found when processing input', file=sys.stderr)
        sys.exit(1)
    clips = data.count(b'OK') - (1 if path.name.startswith('merged_') else 0)
    length = f'{max(clips, 1):.6f}'
    streams = [{'codec_type': 'video', 'start_time': '0.000000', 'duration': length}]
    if b'NOAUDIO' not in data:
        streams.append({'codec_type': 'audio', 'start_time': '0.000000', 'duration': length})
    print(json.dumps({'streams': streams}))


if __name__ == '__main__':
    main()
