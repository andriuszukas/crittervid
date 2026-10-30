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
import logging
import os
import re
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import deployment, log, names
from .config import ACCEL_CHOICES, PipelineConfig
from .ffmpeg import get_ffmpeg_path, get_ffprobe_path, media_arg


EVENT_FILE = re.compile(r'\d{10,}_\d+\.mp4')
SEGMENT_FILE = re.compile(r'\d{10,}_\d+_\d+\.mp4')
# The merge step's file names, shared so the two agree on what it writes
from .names import MERGED_NAME as MERGED_FILE  # noqa: E402

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


_log = log.get('validate')


def say(message, level=None, **extra):
    log.say(_log, message, level, **extra)


def _probe_once(path):
    result = subprocess.run(
        [get_ffprobe_path(), '-v', 'error', '-show_entries', 'format=format_name:stream=codec_type,start_time,duration',
         '-of', 'json', media_arg(path)],
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
        cmd = [get_ffmpeg_path(), '-v', 'error'] + opts + ['-i', media_arg(path), '-f', 'null', '-']
        if subprocess.run(cmd, capture_output=True).returncode == 0:
            return True
    return False


def output_files(config):
    """(converted, merged) .mp4 files under the output folder."""
    converted, merged = [], []
    for dirpath, _, filenames in os.walk(config.output_dir):
        for name in filenames:
            if name.endswith('.mp4'):
                path = Path(dirpath) / name
                (merged if config.merged_dir in path.parents else converted).append(path)
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
        # (in files converted before #77 was fixed), the audio may match either
        if min(abs(audio - video_len), abs(audio - p.end())) > AV_TOLERANCE_S:
            problems.append(f'ERROR: {path}: audio is {audio:.2f} s long but the video '
                            f'is {video_len:.2f} s long (ending at {p.end():.2f} s)')
    if video_start > START_TOLERANCE_S:
        problems.append(f'WARNING: {path}: video starts at {video_start:.2f} s, not 0 '
                        f'(if an earlier version converted it, delete it and convert it again)')
    return problems


def split_set(path):
    """(N, K) for a merged file named ..._part<K>of<N>.mp4, or (None, None) for an
    older name without a part number (a whole day)."""
    m = MERGED_FILE.fullmatch(path.name)
    if m and m.group(2):
        return int(m.group(2)), int(m.group(1))
    return None, None


def check_merged_part(label, part, sources, probes):
    """Compare one merged file with the converted files it was made from."""
    p = probes[part]
    problems = []
    # The merge joins each file up to where its video ends, so a part holds the
    # sum of its files' video ends, less the first file's lead-in: a merged file
    # starts at its first frame
    expected = sum(s.end() for s in sources) - max(sources[0].streams['video'][0], 0)
    length = p.streams['video'][1]
    if abs(length - expected) > MERGED_TOLERANCE_S + MERGED_TOLERANCE_PER_FILE_S * len(sources):
        problems.append(f'ERROR: {label} is {length:.2f} s long, but its {len(sources)} '
                        f'converted files add up to {expected:.2f} s')
    if any('audio' in s.streams for s in sources):
        if 'audio' not in p.streams:
            problems.append(f'ERROR: {part}: no audio, although its converted files have audio')
        elif abs(p.streams['audio'][1] - length) > AV_TOLERANCE_S:
            problems.append(f'ERROR: {part}: audio is {p.streams["audio"][1]:.2f} s long but the video '
                            f'is {length:.2f} s long')
    return problems


def check_merged_days(converted, merged, probes, config):
    """Compare each merged file with the converted files it was made from,
    grouped and split the same way the merge step does it."""
    found = defaultdict(list)
    for path in merged:
        if MERGED_FILE.fullmatch(path.name):   # other names only get the name warning
            found[path.parent.relative_to(config.merged_dir).as_posix()].append(path)

    problems = []
    for day_start, files in sorted(names.group_files_by_day(converted, config.tz, config.clock_offset or 0).items()):
        day = day_start.strftime('%Y/%m/%d')
        parts = found.pop(day, [])
        dated, files = files, [f for _, f in files]
        if any(check_file(f, probes[f]) for f in files):
            continue   # its unreadable or unusable files are reported already
        if not parts:
            problems.append(f'ERROR: no merged file for {day} ({len(files)} converted files)')
            continue
        sets = defaultdict(dict)
        for part in parts:
            n, k = split_set(part)
            sets[n][k] = part
        if len(sets) > 1:
            problems.append(f'WARNING: merged {day} has files from more than one split setting; '
                            f'the older ones repeat the same footage')
        for n, set_parts in sorted(sets.items(), key=lambda item: item[0] or 0):
            if any(check_file(part, probes[part]) for part in set_parts.values()):
                continue   # reported as unusable already
            if n is None:   # an older whole-day name
                [(_, part)] = set_parts.items()
                problems += check_merged_part(f'merged {day}', part, [probes[f] for f in files], probes)
                continue
            for k, start, end, chunk in names.split_day(day_start, dated, n, config.tz):
                chunk = [f for _, f in chunk]
                part = set_parts.get(k)
                if chunk and part is None:
                    problems.append(f'ERROR: merged {day}: part {k} of {n} is missing')
                elif part is not None and not chunk:
                    problems.append(f'WARNING: {part}: no converted files belong in this part')
                elif part is not None:
                    if part.name != names.part_name(start, end, k, n):
                        problems.append(f'WARNING: {part}: part {k} of {n} covers {start:%H:%M} to {end:%H:%M}, '
                                        f'not the times in its name (made by an earlier version? merge again)')
                    problems += check_merged_part(f'merged {day} part {k} of {n}', part,
                                                  [probes[f] for f in chunk], probes)
            problems += [f'WARNING: {part}: part {k} of {n} is not a part the merge writes'
                         for k, part in sorted(set_parts.items()) if not 1 <= k <= n]
    for day in sorted(found):
        if names.unset_clock_day(day):
            problems.append(f'WARNING: {config.merged_dir / day} {names.UNSET_CLOCK_MERGES}')
        else:
            problems.append(f'WARNING: merged files for {day} have no matching converted files')
    return problems


def main(argv=None):
    log.setup_streams()   # UTF-8 on the console, also for option errors
    parser = argparse.ArgumentParser(prog='crittervid validate', description='Check the converted and merged videos.')
    parser.add_argument('--output-dir', type=str, default='output', help='Output directory to check (default: output)')
    parser.add_argument('--workers', type=int, default=4, help='Number of parallel checks (default: 4)')
    parser.add_argument('--deep', action='store_true',
                        help='Also decode every file in full, to check FFmpeg can get through each one (slow: about as long as the footage)')
    parser.add_argument('--accel', choices=ACCEL_CHOICES, default=None,
                        help='Hardware decoding for --deep (default: none); files that fail are retried on the CPU')
    deployment.add_options(parser)
    parser.add_argument('--verbose', action='store_true', help='Also show details on the console (they are always in the logs).')
    args = parser.parse_args(argv)

    config = PipelineConfig(output_dir=args.output_dir, workers=args.workers, deep_validate=args.deep,
                            accel=args.accel, timezone=args.timezone, clock_offset=args.clock_offset,
                            site=args.site, camera=args.camera)
    log.start_run(config, 'validate', verbose=args.verbose, run_log=False)   # it only reads the output folder
    return run(config)


def run(config):
    """Check the videos in config.output_dir; returns 0, 1 if a check found an
    error, or 2 for settings it can't use."""
    if config.workers < 1:
        message = 'crittervid validate: error: --workers must be at least 1'
        print(message, file=sys.stderr)
        log.file_only(_log, logging.ERROR, message)
        return 2
    if not config.output_dir.is_dir():
        say(f'ERROR: output folder not found: {config.output_dir}')
        return 1
    # It only reads the output folder, so options given here aren't saved
    try:
        config = deployment.settle(config, save=False)
    except deployment.Invalid as e:
        say(f"ERROR: {e}")
        return 2

    converted, merged = output_files(config)
    if not converted and not merged:
        say(f'No output videos in {config.output_dir} to check.')
        return 0
    deep = config.deep_validate
    if not get_ffprobe_path():
        say('ffprobe not found (it comes with FFmpeg): only checking that every file decodes. '
            'Install ffprobe for the full checks of audio, lengths and merged days.', level=logging.WARNING)
        deep = True
    say(f'Checking {len(converted)} converted and {len(merged)} merged videos'
        f'{" (decoding each in full)" if deep else ""}...')

    problems = check_names(converted, merged)
    try:
        if get_ffprobe_path():
            with ThreadPoolExecutor(max_workers=config.workers) as executor:
                probes = dict(zip(converted + merged, executor.map(probe, converted + merged)))
            for path in converted:
                problems += check_converted(path, probes[path])
            for path in merged:
                problems += check_file(path, probes[path])
            if merged:
                problems += check_merged_days(converted, merged, probes, config)
            elif converted:
                say('No merged files yet, so merged days are not checked.')
        if deep:
            from .ffmpeg import ACCEL_OPTIONS, detect_accel
            accel = detect_accel() if config.accel == 'auto' else config.accel
            input_opts = ACCEL_OPTIONS.get(accel, ([], []))[0]
            with ThreadPoolExecutor(max_workers=config.workers) as executor:
                results = list(executor.map(lambda p: decodes(p, input_opts), converted + merged))
            problems += [f'ERROR: {p}: does not decode' for p, ok in zip(converted + merged, results) if not ok]
    except KeyboardInterrupt:
        say('\nValidation interrupted by user (Ctrl+C). Partial results shown below.', level=logging.WARNING)
        problems.append('ERROR: validation stopped by user before it finished')

    for line in problems:
        say(line)
    errors = sum(1 for line in problems if line.startswith('ERROR'))
    if errors:
        say(f'\nValidation found {errors} problem{"s" if errors != 1 else ""}.', level=logging.ERROR)
        if any(line.startswith('ERROR') and 'merged' in line for line in problems):
            say('Merged files are skipped while their events stay the same; to make them again, merge with '
                '--force (process_all.py --force-merge, or "Merge everything again" in the app).')
        return 1
    say('All output videos passed the checks.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
