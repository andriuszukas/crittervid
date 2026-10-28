"""
Stand-in for ffprobe used by the tests, reading the stand-in files that
fake_ffmpeg.py writes (b'OK' followed by the input's content).

Each file is given a video and an audio stream starting at 0, in an MP4
container. Its length in seconds is the number of clips in it: an output's
content is b'OK' + its input, so a converted file contains as many b'OK' as
clips, and a merged file (b'OK' + its inputs) one more. A file that doesn't start
with b'OK' is unreadable (exit 1).

Markers in the content describe imperfect files:
- b'NOAUDIO': no audio stream
- b'NOVIDEO': no video stream
- b'LATESTART': the video starts at 0.8 s (in a merged file, whose inputs are cut
  at their video's end, that adds 0.8 s to the length for each marker)
- b'AUDIOSHORT': the audio is half as long as the video
- b'LATEAUDIOEND': the video starts at 0.8 s and the audio runs from 0 to the
  video's end, as in real files whose first frame is placed late (#77)
- b'PLUS50MS': 50 ms longer, per marker (to test tolerances finer than 1 s)
- b'NODURATION': the streams have no duration field
- b'NOTMP4': the container is Matroska

Answers the queries the pipeline makes: `-show_entries stream=...[:format=...]`
with `-of json`, and `-of csv=p=0` (the requested values of the streams chosen by
-select_streams, comma-separated, one line per stream).

If FAKE_FFPROBE_FAIL_ONCE names a file that exists, a call deletes it and fails
as if the file were unreadable, to test retries.
"""
import json
import os
import sys
from pathlib import Path

ERROR = 'Invalid data found when processing input'


def main():
    args = sys.argv[1:]
    if args == ['-version']:
        print('ffprobe version 0.0-fake Copyright (c) the CritterVid tests')
        return
    path = Path(args[-1])
    flag = os.environ.get('FAKE_FFPROBE_FAIL_ONCE')
    if flag:
        try:
            os.remove(flag)
        except OSError:
            pass
        else:
            print(f'{path}: {ERROR}', file=sys.stderr)
            sys.exit(1)
    try:
        data = path.read_bytes()
    except OSError:
        sys.exit(1)
    if not data.startswith(b'OK'):
        print(f'{path}: {ERROR}', file=sys.stderr)
        sys.exit(1)

    merged = path.name.startswith('merged_')
    clips = data.count(b'OK') - (1 if merged else 0)
    length = float(max(clips, 1))
    start = 0.0
    length += 0.05 * data.count(b'PLUS50MS')
    if b'LATESTART' in data:
        if merged:
            length += 0.8 * data.count(b'LATESTART')
        else:
            start = 0.8
    audio_length = length
    if b'LATEAUDIOEND' in data and not merged:
        start = 0.8
        audio_length = start + length

    def stream(kind, stream_length):
        fields = {'codec_type': kind, 'start_time': f'{start if kind == "video" else 0.0:.6f}'}
        if b'NODURATION' not in data:
            fields['duration'] = f'{stream_length:.6f}'
        return fields

    streams = []
    if b'NOVIDEO' not in data:
        streams.append(stream('video', length))
    if b'NOAUDIO' not in data:
        streams.append(stream('audio', length / 2 if b'AUDIOSHORT' in data else audio_length))

    entries = args[args.index('-show_entries') + 1] if '-show_entries' in args else ''
    wanted = {}
    for section in entries.split(':'):
        kind, _, names = section.partition('=')
        wanted[kind] = names.split(',') if names else []

    if '-select_streams' in args:
        kind = args[args.index('-select_streams') + 1][0]
        streams = [s for s in streams if s['codec_type'].startswith(kind)][:1]

    output = args[args.index('-of') + 1] if '-of' in args else 'default'
    if output.startswith('csv'):
        for s in streams:
            print(','.join(s.get(name, 'N/A') for name in wanted.get('stream', [])))
        return
    result = {'streams': [{k: v for k, v in s.items() if k in wanted.get('stream', [])} for s in streams]}
    if 'format' in wanted:
        result['format'] = {'format_name': 'matroska,webm' if b'NOTMP4' in data else 'mov,mp4,m4a,3gp,3g2,mj2'}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
