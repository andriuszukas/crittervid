"""Check the pipeline's output.

By default the check is fast: ffprobe reads each file's streams and lengths, and
the results are compared with what they should be.
- Event files (<timestamp>_<duration>.mp4) have video and audio of matching length.
- Per-segment files (<event>/<event>_<offset>.mp4) are readable and have video.
- Each merged day is as long as the converted files it was made from.
- Every .mp4 has one of the names the pipeline writes.
--deep also decodes every file in full (once each), which takes about as long as
the footage itself and is only needed to look for damage inside the video.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ffmpeg_helper import get_ffmpeg_path, get_ffprobe_path

OUTPUT_ROOT = Path('output')
MERGED_ROOT = Path('output/merged')

EVENT_FILE = re.compile(r'\d{10,}_\d{4}\.mp4')
SEGMENT_FILE = re.compile(r'\d{10,}_\d{4}_\d+\.mp4')
MERGED_FILE = re.compile(r'merged_\d{8}_\d{4}_\d{8}_\d{4}(_part\d+of\d+)?\.mp4')

# An event's audio is cut to its video's span, but AAC comes in 128 ms blocks
AV_TOLERANCE_S = 0.5
# A merged day ends each file where its video ends, so it should match the sum
# of its files almost exactly; allow a little rounding per file
MERGED_TOLERANCE_S = 0.1
MERGED_TOLERANCE_PER_FILE_S = 0.005
START_TOLERANCE_S = 0.05


def probe(path):
    """{codec_type: (start_time, duration)} for a file's streams, or None if
    ffprobe can't read it."""
    result = subprocess.run(
        [FFPROBE, '-v', 'error', '-show_entries', 'stream=codec_type,start_time,duration',
         '-of', 'json', str(path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode != 0:
        return None
    try:
        streams = json.loads(result.stdout).get('streams', [])
        return {s['codec_type']: (float(s.get('start_time', 0) or 0), float(s.get('duration', 0) or 0))
                for s in streams if s.get('codec_type') in ('video', 'audio')}
    except (ValueError, KeyError, AttributeError):
        return None


def decodes(path, input_opts):
    """True if FFmpeg can decode the whole file without errors."""
    cmd = [get_ffmpeg_path(), '-v', 'error'] + input_opts + ['-i', str(path), '-f', 'null', '-']
    return subprocess.run(cmd, capture_output=True).returncode == 0


def output_files():
    """(converted, merged) .mp4 files under the output folder."""
    converted, merged = [], []
    for dirpath, _, filenames in os.walk(OUTPUT_ROOT):
        for name in filenames:
            if name.endswith('.mp4'):
                path = Path(dirpath) / name
                (merged if MERGED_ROOT in path.parents else converted).append(path)
    return sorted(converted), sorted(merged)


def check_names(converted, merged):
    problems = []
    for path in converted:
        if not (EVENT_FILE.fullmatch(path.name) or SEGMENT_FILE.fullmatch(path.name)):
            problems.append(f'WARNING: {path}: not a name the converter writes')
    for path in merged:
        if not MERGED_FILE.fullmatch(path.name):
            problems.append(f'WARNING: {path}: not a name the merge step writes')
    return problems


def check_converted(path, streams):
    if streams is None:
        return [f'ERROR: {path}: unreadable']
    if 'video' not in streams:
        return [f'ERROR: {path}: no video']
    problems = []
    if EVENT_FILE.fullmatch(path.name):
        video_start, video_len = streams['video']
        if 'audio' not in streams:
            problems.append(f'ERROR: {path}: no audio track')
        elif abs(streams['audio'][1] - (video_start + video_len)) > AV_TOLERANCE_S:
            problems.append(f'ERROR: {path}: audio is {streams["audio"][1]:.2f} s long but the video '
                            f'ends at {video_start + video_len:.2f} s')
        if video_start > START_TOLERANCE_S:
            problems.append(f'WARNING: {path}: video starts at {video_start:.2f} s, not 0')
    return problems


def check_merged_days(converted, merged, probes):
    """Compare each merged day with the converted files it was made from, grouped
    the same way the merge step groups them."""
    import merge_videos
    merge_videos.OUTPUT_ROOT, merge_videos.MERGED_ROOT = OUTPUT_ROOT, MERGED_ROOT
    expected = {}
    for day_start, files in merge_videos.group_files_by_day(converted).items():
        streams = [probes.get(f) for _, f in files]
        if any(s is None or 'video' not in s for s in streams):
            continue   # reported as unreadable already
        expected[day_start.strftime('%Y/%m/%d')] = (
            len(files), sum(s['video'][1] for s in streams), any('audio' in s for s in streams))
    found = defaultdict(list)
    for path in merged:
        found[path.parent.relative_to(MERGED_ROOT).as_posix()].append(path)

    problems = []
    for day, (count, total, has_audio) in sorted(expected.items()):
        parts = found.pop(day, [])
        if not parts:
            problems.append(f'ERROR: no merged file for {day} ({count} converted files)')
            continue
        streams = [probes.get(p) for p in parts]
        if any(s is None or 'video' not in s for s in streams):
            continue
        merged_total = sum(s['video'][1] for s in streams)
        if abs(merged_total - total) > MERGED_TOLERANCE_S + MERGED_TOLERANCE_PER_FILE_S * count:
            problems.append(f'ERROR: merged {day} is {merged_total:.2f} s long, but its {count} '
                            f'converted files add up to {total:.2f} s')
        if has_audio and not all('audio' in s for s in streams):
            problems.append(f'ERROR: merged {day} has no audio, although its converted files do')
    for day in sorted(found):
        problems.append(f'WARNING: merged files for {day} have no matching converted files')
    return problems


def main():
    # Paths can contain characters the console's code page can't encode (e.g. the
    # Windows default when output is piped to the GUI), so always write UTF-8.
    # The streams are None when there's no console at all (pythonw).
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Check the converted and merged videos.')
    parser.add_argument('--output-dir', type=str, default='output', help='Output directory to check (default: output)')
    parser.add_argument('--workers', type=int, default=4, help='Number of parallel checks (default: 4)')
    parser.add_argument('--deep', action='store_true',
                        help='Also decode every file in full to look for damage inside the video (slow: about as long as the footage)')
    parser.add_argument('--accel', choices=['cpu', 'cuda', 'qsv', 'vaapi', 'amf', 'auto'], default=None,
                        help='Hardware decoding for --deep (default: none)')
    args = parser.parse_args()

    global OUTPUT_ROOT, MERGED_ROOT, FFPROBE
    OUTPUT_ROOT = Path(args.output_dir)
    MERGED_ROOT = OUTPUT_ROOT / 'merged'
    FFPROBE = get_ffprobe_path()

    converted, merged = output_files()
    deep = args.deep
    if not FFPROBE:
        print('ffprobe not found (it comes with FFmpeg); checking by decoding every file instead.')
        deep = True
    print(f'Checking {len(converted)} converted and {len(merged)} merged videos'
          f'{" (decoding each in full)" if deep else ""}...')

    problems = check_names(converted, merged)
    try:
        if FFPROBE:
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                probes = dict(zip(converted + merged, executor.map(probe, converted + merged)))
            for path in converted:
                problems += check_converted(path, probes[path])
            for path in merged:
                if probes[path] is None:
                    problems.append(f'ERROR: {path}: unreadable')
            if merged:
                problems += check_merged_days(converted, merged, probes)
            elif converted:
                print('No merged files yet, so merged days are not checked.')
        if deep:
            from convert_media import ACCEL_OPTIONS
            input_opts = ACCEL_OPTIONS.get(args.accel, ([], []))[0]
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                results = list(executor.map(lambda p: decodes(p, input_opts), converted + merged))
            problems += [f'ERROR: {p}: does not decode cleanly'
                         for p, ok in zip(converted + merged, results) if not ok]
    except KeyboardInterrupt:
        print('\nValidation interrupted by user (Ctrl+C). Partial results shown below.')
        problems.append('ERROR: validation stopped by user before it finished')

    for line in problems:
        print(line)
    errors = sum(1 for line in problems if line.startswith('ERROR'))
    if errors:
        print(f'\nValidation found {errors} problem{"s" if errors != 1 else ""}.')
        return 1
    print('All output videos passed the checks.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
