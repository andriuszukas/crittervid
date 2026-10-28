import json
import os

from pathlib import Path
import subprocess
from datetime import datetime, timedelta
import re
import argparse
import sys
from . import media_format
from .config import PipelineConfig
from .ffmpeg import PATH_TEXT, get_ffmpeg_path, get_ffprobe_path, media_arg
from .names import (FILENAME_RE, MERGED_NAME, MINUTES_PER_DAY, UNSET_CLOCK_MERGES, file_start,  # noqa: F401
                    group_files_by_day, part_name, split_day, unset_clock_day, unset_clock_files)


# Each day's merged folder keeps a record of the converted files each merged file
# was made from, so older merged files are only deleted when nothing is lost
CONTENTS_NAME = 'merged_contents.json'

def find_mp4_files(root):
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if filename.endswith('.mp4'):
                yield Path(dirpath) / filename

def video_end(path):
    """When a file's video ends (its start time plus its length) in seconds, or
    None if ffprobe isn't available or can't read it. Usually the video starts
    at 0; when the first frame is placed later, the file still runs until then
    plus its length."""
    if not get_ffprobe_path():
        return None
    result = subprocess.run([get_ffprobe_path(), '-v', 'error', '-select_streams', 'v:0',
                             '-show_entries', 'stream=start_time,duration', '-of', 'csv=p=0', media_arg(path)],
                            capture_output=True, text=True, encoding='utf-8', errors='replace')
    try:
        start, length = (float(x) for x in result.stdout.strip().split(','))
        return max(start, 0) + length
    except ValueError:
        return None


def up_to_date(out_path, sources, files, record):
    """Whether a merged file already holds exactly these converted files: the
    record says it was made from them, and each is older than it. Strictly: some
    drives keep times to only 2 s, so a file converted again just before the merge
    can show the same time; a tie costs at most a needless merge, never a stale
    file. A file replaced by hand isn't noticed."""
    try:
        made = out_path.stat().st_mtime
        return record.get(out_path.name) == sources and all(f.stat().st_mtime < made for _, f in files)
    except OSError:
        return False


def merge_group(start, end, files, summary, record, k, n, config):
    out_dir = config.merged_dir / start.strftime('%Y/%m/%d')
    out_dir.mkdir(parents=True, exist_ok=True)
    start_str = start.strftime('%Y%m%d_%H%M')
    out_name = part_name(start, end, k, n)
    out_path = out_dir / out_name
    sources = [source_name(f, config) for _, f in files]
    if not config.force_merge and up_to_date(out_path, sources, files, record):
        msg = f"SKIPPED: {out_path} is up to date ({len(files)} files)"
        print(msg)
        summary.append(msg)
        return out_path
    # Write to a temp file and rename on success, so a failed merge never leaves
    # a broken file that looks like a finished one
    tmp_path = out_dir / (out_name + '.part')
    # Create filelist.txt for ffmpeg concat
    filelist_path = out_dir / f"filelist_{start_str}.txt"
    # The list has one path per line, so a path with a line break can't be in it
    broken = [file for _, file in files if '\n' in str(file) or '\r' in str(file)]
    if broken:
        error_msg = (f"Failed to merge {out_name}: {broken[0]} has a line break in its path, "
                     f"which FFmpeg's list of files to join can't hold; rename it")
        print(error_msg)
        summary.append(f"ERROR: {error_msg}")
        return False
    # FFmpeg reads the list as UTF-8 (other bytes as they are), and a quote inside
    # a quoted path must be written as '\''
    with open(filelist_path, 'w', **PATH_TEXT) as f:
        for _, file in files:
            escaped = str(file.resolve()).replace("'", "'\\''")
            # inpoint 0: start each file at its own time zero. Without it, the AAC
            # encoder's start padding (before zero) delays the video at each join
            f.write(f"file '{escaped}'\ninpoint 0\n")
            # End each file exactly where its video ends. AAC audio comes in blocks
            # and the video's last frame has an estimated length, so the two rarely
            # end together; left to itself, FFmpeg starts the next file after one
            # or the other depending on the version, which adds up over many files
            video_stop = video_end(file)
            if video_stop:
                f.write(f"outpoint {video_stop:.6f}\nduration {video_stop:.6f}\n")
    # Copy the video; re-encode the audio once, so the small padding at the start
    # of each segment's AAC track doesn't add up into drift over a long merge
    cmd = [get_ffmpeg_path(), '-y', '-f', 'concat', '-safe', '0', '-i', media_arg(filelist_path), '-c:v', 'copy', '-c:a', 'aac',
           *media_format.creation_time_args(file_start(files[0][1])), '-f', 'mp4', media_arg(tmp_path)]
    result = subprocess.run(cmd, capture_output=True)
    filelist_path.unlink()
    if result.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        error_msg = f"Failed to merge {out_name}: {result.stderr.decode('utf-8', errors='replace')}"
        print(error_msg)
        summary.append(f"ERROR: {error_msg}")
        return False
    # Record what the new file holds before it replaces the old one, together
    # with what the old one held: whenever the run stops, the record lists at
    # least what the file holds, which only ever keeps a file that could have
    # been deleted, never the other way round
    previous = record.get(out_name)
    record[out_name] = sorted(set(previous or []) | set(sources))

    def fail(reason):
        tmp_path.unlink(missing_ok=True)
        error_msg = f"Failed to merge {out_name}: {reason}"
        print(error_msg)
        summary.append(f"ERROR: {error_msg}")
        return False

    if not write_contents(out_dir, record, summary):
        if previous is None:
            record.pop(out_name)
        else:
            record[out_name] = previous
        return fail("couldn't record its contents")
    try:
        tmp_path.replace(out_path)
    except OSError as e:   # e.g. the old file is open in a video player
        # The old file stays, so describe it again: its record mustn't claim the
        # new events, or a later run could take it for up to date
        if previous is None:
            record.pop(out_name)
        else:
            record[out_name] = previous
        write_contents(out_dir, record, summary)
        return fail(f"couldn't replace the earlier file: {e}")
    record[out_name] = sources   # written with the day's other changes
    success_msg = f"Merged {len(files)} files -> {out_path}"
    print(success_msg)
    summary.append(f"SUCCESS: {success_msg}")
    return out_path


def source_name(path, config):
    """How the contents record names a converted file: its path in the output
    folder, with forward slashes on every system."""
    return Path(os.path.relpath(path, config.output_dir)).as_posix()


def read_contents(day_dir):
    """The day's record of {merged file name: [converted files]}; empty if there
    is none or it can't be read."""
    try:
        record = json.loads((day_dir / CONTENTS_NAME).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(record, dict):
        return {}
    # Leave out anything malformed, so those files count as unrecorded and are kept
    return {name: sources for name, sources in record.items()
            if isinstance(sources, list) and sources and all(isinstance(s, str) for s in sources)}


def write_contents(day_dir, record, summary):
    path = day_dir / CONTENTS_NAME
    tmp = day_dir / (CONTENTS_NAME + '.part')
    try:
        if not record:
            path.unlink(missing_ok=True)
            return True
        tmp.write_text(json.dumps(record, indent=1, sort_keys=True), encoding='utf-8')
        tmp.replace(path)
        return True
    except OSError as e:
        tmp.unlink(missing_ok=True)
        msg = f"couldn't write {path}: {e}"
        print(f"WARNING: {msg}")
        summary.append(f"WARNING: {msg}")
        return False


def remove_stale_merges(day_dir, written, summary, day_files, record, config):
    """Delete merged files left in a day's folder by an earlier --splits-per-day
    setting: they repeat the footage this run just merged.

    Only files with the exact names this step writes are considered. An older set
    of parts is deleted only if the record shows every file in it was made from
    converted files that are still there (and so are in this run's merge); a set
    holding footage whose converted files are gone (e.g. deleted to save space),
    or made by an earlier version that kept no record, is left with a warning. A
    file that can't be deleted (e.g. open in a video player) is left too.
    Updates the record to match what's left."""
    def warn(msg):
        print(f"WARNING: {msg}")
        summary.append(f"WARNING: {msg}")

    sets = {}
    for f in sorted(day_dir.glob('merged_*.mp4')):
        m = MERGED_NAME.fullmatch(f.name)
        if f not in written and m:
            sets.setdefault(m.group(2), []).append(f)
    current = {source_name(f, config) for f in day_files}
    for n, stale in sorted(sets.items(), key=lambda item: item[0] or ''):
        setting = f'{n} per day' if n else 'one per day'
        kept = f"kept {len(stale)} merged files ({setting}) in {day_dir} from an earlier split setting"
        if any(f.name not in record for f in stale):
            warn(f"{kept}: they were made by an earlier version that didn't record their contents; "
                 f"delete them yourself if they repeat footage")
            continue
        gone = sorted({s for f in stale for s in record[f.name]} - current)
        if gone:
            warn(f"{kept}: they hold footage whose converted files are gone ({', '.join(gone[:3])}"
                 f"{', ...' if len(gone) > 3 else ''})")
            continue
        for old in stale:
            try:
                old.unlink()
            except OSError as e:
                warn(f"couldn't remove {old} (left from an earlier split setting): {e}")
                continue
            msg = f"Removed {old} (left from an earlier split setting)"
            print(msg)
            summary.append(f"REMOVED: {msg}")
    # Forget files that are no longer there (e.g. deleted by hand)
    for name in [name for name in record if not (day_dir / name).is_file()]:
        del record[name]

def main(argv=None):
    # Paths can contain characters the console's code page can't encode (e.g. the
    # Windows default when output is piped to the GUI), so always write UTF-8.
    # The streams are None when there's no console at all (pythonw).
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(prog='crittervid merge', description="Merge converted .mp4 files into one or more clips per day.")
    parser.add_argument('--output-dir', type=str, default='output', help='Output directory containing .mp4 files to merge (default: output)')
    parser.add_argument('--splits-per-day', type=int, default=1, help='Number of equal time windows to divide each day into, one merged .mp4 file per window '
                             'that has events (default: 1, i.e. all of a day\'s files merged into one). 1 to 1440.')
    parser.add_argument('--force', action='store_true', help='Merge every part again, including ones that are '
                        'already up to date (by default they are skipped), e.g. after upgrading to a '
                        'version that merges differently.')
    args = parser.parse_args(argv)

    # Update global paths based on arguments
    return run(PipelineConfig(output_dir=args.output_dir, splits_per_day=args.splits_per_day, force_merge=args.force))


def run(config):
    """Merge the converted files in config.output_dir into days (or parts of
    days); returns 0, 1 if a part failed, or 2 for a split it can't use."""
    if not 1 <= config.splits_per_day <= MINUTES_PER_DAY:
        print(f'crittervid merge: error: --splits-per-day must be 1 to {MINUTES_PER_DAY} (windows are whole minutes)',
              file=sys.stderr)
        return 2

    mp4_files = list(find_mp4_files(config.output_dir))
    groups = group_files_by_day(mp4_files)
    summary = []
    failed = 0
    def warn(msg):
        print(f"WARNING: {msg}")
        summary.append(f"WARNING: {msg}")

    # Files the converter didn't write (e.g. copied in by hand) aren't merged; say so
    unknown = sorted(f for f in mp4_files if not FILENAME_RE.search(str(f)) and config.merged_dir not in f.parents)
    if unknown:
        warn(f"{len(unknown)} .mp4 files weren't merged: their names aren't ones the converter writes "
             f"(<start time>_<length>[_<offset>].mp4), e.g. {unknown[0]}")
    unset = sorted(unset_clock_files(mp4_files))
    if unset:
        events = {FILENAME_RE.search(str(f)).group(1) for f in unset}
        warn(f"{len(events)} events weren't merged: they were recorded before the camera's "
             f"clock was set, so their real time is unknown (e.g. {unset[0]})")
    # Earlier versions merged such events into a day of their own; say so rather
    # than delete it, as the record that would show what it holds doesn't exist
    for day_dir in sorted(config.merged_dir.glob('*/*/*')):
        if (day_dir.is_dir() and unset_clock_day(day_dir.relative_to(config.merged_dir).as_posix())
                and any(MERGED_NAME.fullmatch(f.name) for f in day_dir.iterdir())):
            warn(f"{day_dir} {UNSET_CLOCK_MERGES}")
    n = config.splits_per_day
    for day_start, files in groups.items():
        if config.cancel.is_cancelled():   # the run is being stopped: merge nothing more
            warn("merging stopped before it finished; run it again to merge the rest")
            failed += 1
            break
        day_dir = config.merged_dir / day_start.strftime('%Y/%m/%d')
        record = read_contents(day_dir)
        written, day_failed = set(), False
        for k, start, end, split_files in split_day(day_start, files, n):
            if not split_files:
                continue
            out_path = merge_group(start, end, split_files, summary, record, k, n, config)
            if out_path:
                written.add(out_path)
            else:
                failed += 1
                day_failed = True
        if not day_failed:   # keep older files if this day's merge didn't complete
            remove_stale_merges(day_dir, written, summary, [f for _, f in files], record, config)
        if day_dir.is_dir():
            write_contents(day_dir, record, summary)
    # Write summary report
    config.merged_dir.mkdir(parents=True, exist_ok=True)
    with open(config.merge_summary, 'w', **PATH_TEXT) as f:
        for line in summary:
            f.write(line + '\n')
    print(f"Summary report written to {config.merge_summary}")

    return 1 if failed else 0

if __name__ == '__main__':
    sys.exit(main())
