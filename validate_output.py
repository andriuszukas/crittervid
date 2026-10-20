"""Check the pipeline's output.

By default the check is fast: ffprobe reads each file's format, streams and
lengths, and the results are compared with what they should be.
- Event files (<timestamp>_<duration>.mp4) are MP4 with video and audio of
  matching length.
- Per-segment files (<event>/<event>_<offset>.mp4) are MP4 with video.
- Each merged day is as long as the converted files it was made from, and keeps
  their audio.
- Every .mp4 has one of the names the pipeline writes.
--deep also decodes every file in full (once each), which takes about as long as
the footage itself and only shows whether FFmpeg can get through each file.
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
FFPROBE = None

EVENT_FILE = re.compile(r'\d{10,}_\d{4}\.mp4')
SEGMENT_FILE = re.compile(r'\d{10,}_\d{4}_\d+\.mp4')
MERGED_FILE = re.compile(r'merged_\d{8}_\d{4}_\d{8}_\d{4}(?:_part\d+of(\d+))?\.mp4')

# An event's audio is cut to its video's span; AAC's 128 ms blocks and the last
# frame's estimated length leave at most a few hundredths of a second between them
AV_TOLERANCE_S = 0.2
# A merged day ends each file where its video ends, so it matches the sum of its
# files to within rounding
MERGED_TOLERANCE_S = 0.02
MERGED_TOLERANCE_PER_FILE_S = 0.001
START_TOLERANCE_S = 0.05


class Probe:
    """What ffprobe reports about a file: its format name and, per stream type,
    (start time, length in seconds or None if unknown). `error` is set instead
    if ffprobe couldn't read the file."""

    def __init__(self, format_name='', streams=None, error=None):
        self.format_name, self.streams, self.error = format_name, streams or {}, error

    def end(self, kind='video'):
        start, length = self.streams[kind]
        return None if length is None else max(start, 0) + length


def _probe_once(path):
    result = subprocess.run(
        [FFPROBE, '-v', 'error', '-show_entries', 'format=format_name:stream=codec_type,start_time,duration',
         '-of', 'json', str(path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode != 0:
        lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
        return Probe(error=lines[0] if lines else f'ffprobe exited with code {result.returncode}')
    try:
        info = json.loads(result.stdout)
        streams = {}
        for s in info.get('streams', []):
            if s.get('codec_type') in ('video', 'audio') and s['codec_type'] not in streams:
                length = s.get('duration')
                streams[s['codec_type']] = (float(s.get('start_time') or 0),
                                            float(length) if length not in (None, 'N/A') else None)
        return Probe(info.get('format', {}).get('format_name', ''), streams)
    except (ValueError, KeyError, AttributeError, TypeError) as e:
        return Probe(error=f"ffprobe's output couldn't be read ({e})")


def probe(path):
    """Probe a file, trying once more if ffprobe fails (it can, briefly, when
    the system is busy)."""
    result = _probe_once(path)
    return _probe_once(path) if result.error else result


def decodes(path, input_opts):
    """True if FFmpeg can decode the whole file; with hardware decoding options, a
    file that fails is tried again on the CPU before giving up."""
    for opts in ([input_opts, []] if input_opts else [[]]):
        cmd = [get_ffmpeg_path(), '-v', 'error'] + opts + ['-i', str(path), '-f', 'null', '-']
        if subprocess.run(cmd, capture_output=True).returncode == 0:
            return True
    return False


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


def check_file(path, p):
    """Problems with one file that make it unusable: unreadable, not MP4, no
    video, or a length ffprobe can't tell."""
    if p.error:
        return [f'ERROR: {path}: unreadable ({p.error})']
    if 'mp4' not in p.format_name.split(','):
        return [f'ERROR: {path}: not an MP4 file ({p.format_name or "unknown format"})']
    if 'video' not in p.streams:
        return [f'ERROR: {path}: no video']
    if any(length is None for _, length in p.streams.values()):
        return [f'ERROR: {path}: length unknown']
    return []


def check_converted(path, p):
    problems = check_file(path, p)
    if problems or not EVENT_FILE.fullmatch(path.name):
        return problems
    video_start, video_len = p.streams['video']
    if 'audio' not in p.streams:
        problems.append(f'ERROR: {path}: no audio track')
    else:
        audio = p.streams['audio'][1]
        # The audio covers the video from its first frame to its end. Usually the
        # video starts at 0, so length and end are the same; when it starts later
        # (#77), the audio may match either
        if min(abs(audio - video_len), abs(audio - p.end())) > AV_TOLERANCE_S:
            problems.append(f'ERROR: {path}: audio is {audio:.2f} s long but the video '
                            f'is {video_len:.2f} s long (ending at {p.end():.2f} s)')
    if video_start > START_TOLERANCE_S:
        problems.append(f'WARNING: {path}: video starts at {video_start:.2f} s, not 0')
    return problems


def split_set(path):
    """The --splits-per-day setting a merged file was made with (1 if none)."""
    m = MERGED_FILE.fullmatch(path.name)
    return int(m.group(1)) if m and m.group(1) else 1


def check_merged_days(converted, merged, probes):
    """Compare each merged day with the converted files it was made from, grouped
    the same way the merge step groups them."""
    import merge_videos
    merge_videos.OUTPUT_ROOT, merge_videos.MERGED_ROOT = OUTPUT_ROOT, MERGED_ROOT
    found = defaultdict(list)
    for path in merged:
        found[path.parent.relative_to(MERGED_ROOT).as_posix()].append(path)

    problems = []
    for day_start, files in sorted(merge_videos.group_files_by_day(converted).items()):
        day = day_start.strftime('%Y/%m/%d')
        parts = found.pop(day, [])
        sources = [probes[f] for _, f in files]
        if any(check_file(f, p) for (_, f), p in zip(files, sources)):
            continue   # its unreadable or unusable files are reported already
        if not parts:
            problems.append(f'ERROR: no merged file for {day} ({len(files)} converted files)')
            continue
        total = sum(p.end() for p in sources)
        has_audio = any('audio' in p.streams for p in sources)
        sets = defaultdict(list)
        for part in parts:
            sets[split_set(part)].append(part)
        if len(sets) > 1:
            problems.append(f'WARNING: merged {day} has files from more than one split setting '
                            f'({", ".join(str(n) for n in sorted(sets))} per day); older ones repeat the same footage')
        for n, set_parts in sorted(sets.items()):
            if any(check_file(part, probes[part]) for part in set_parts):
                continue   # reported as unusable already
            merged_total = sum(probes[part].end() for part in set_parts)
            difference = abs(merged_total - total)
            tolerance = MERGED_TOLERANCE_S + MERGED_TOLERANCE_PER_FILE_S * len(files)
            # Converted files whose video starts late (#77) can make the merged day
            # up to that much shorter: explain such a difference instead of failing
            late = sum(p.streams['video'][0] for p in sources if p.streams['video'][0] > START_TOLERANCE_S)
            label = f'merged {day}' + (f' ({n} per day)' if len(sets) > 1 else '')
            if difference > tolerance + late:
                problems.append(f'ERROR: {label} is {merged_total:.2f} s long, but its {len(files)} '
                                f'converted files add up to {total:.2f} s')
            elif difference > tolerance:
                problems.append(f'WARNING: {label} is {merged_total:.2f} s long, {difference:.2f} s different '
                                f'from its converted files, because some of their videos start late (#77)')
            if has_audio:
                problems += [f'ERROR: {part}: no audio, although its converted files have audio'
                             for part in set_parts if 'audio' not in probes[part].streams]
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
                        help='Also decode every file in full, to check FFmpeg can get through each one (slow: about as long as the footage)')
    parser.add_argument('--accel', choices=['cpu', 'cuda', 'qsv', 'vaapi', 'amf', 'auto'], default=None,
                        help='Hardware decoding for --deep (default: none); files that fail are retried on the CPU')
    args = parser.parse_args()

    global OUTPUT_ROOT, MERGED_ROOT, FFPROBE
    OUTPUT_ROOT = Path(args.output_dir)
    MERGED_ROOT = OUTPUT_ROOT / 'merged'
    if not OUTPUT_ROOT.is_dir():
        print(f'ERROR: output folder not found: {OUTPUT_ROOT}')
        return 1
    FFPROBE = get_ffprobe_path()

    converted, merged = output_files()
    if not converted and not merged:
        print(f'No output videos in {OUTPUT_ROOT} to check.')
        return 0
    deep = args.deep
    if not FFPROBE:
        print('ffprobe not found (it comes with FFmpeg): only checking that every file decodes. '
              'Install ffprobe for the full checks of audio, lengths and merged days.')
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
                problems += check_file(path, probes[path])
            if merged:
                problems += check_merged_days(converted, merged, probes)
            elif converted:
                print('No merged files yet, so merged days are not checked.')
        if deep:
            from convert_media import ACCEL_OPTIONS, detect_accel
            accel = detect_accel() if args.accel == 'auto' else args.accel
            input_opts = ACCEL_OPTIONS.get(accel, ([], []))[0]
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                results = list(executor.map(lambda p: decodes(p, input_opts), converted + merged))
            problems += [f'ERROR: {p}: does not decode' for p, ok in zip(converted + merged, results) if not ok]
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
