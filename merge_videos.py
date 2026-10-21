import json
import os

from pathlib import Path
import subprocess
from datetime import datetime, timedelta
import re
import argparse
import sys
from ffmpeg_helper import get_ffmpeg_path, get_ffprobe_path
import media_format

OUTPUT_ROOT = Path('output')
MERGED_ROOT = Path('output/merged')
FFMPEG_PATH = get_ffmpeg_path()  # Auto-detect ffmpeg location
FFPROBE_PATH = get_ffprobe_path()
SEGMENT_DURATION_HOURS = 12
SUMMARY_REPORT = MERGED_ROOT / 'merge_summary.txt'

# Regex to extract timestamp and group ID from filename
# Converted files: one per event (<timestamp>_<duration>.mp4) for the cameras'
# record format, or one per segment (<timestamp>_<duration>_<offset>.mp4)
FILENAME_RE = re.compile(r'(?:^|[\\/])(\d{10,})_(\d{4})(?:_\d+)?\.mp4$')
# Names of the files this step writes: merged_<start>_<end>[_part<K>of<N>].mp4
MERGED_NAME = re.compile(r'merged_\d{8}_\d{4}_\d{8}_\d{4}(?:_part(\d+)of(\d+))?\.mp4')
# Each day's merged folder keeps a record of the converted files each merged file
# was made from, so older merged files are only deleted when nothing is lost
CONTENTS_NAME = 'merged_contents.json'

def find_mp4_files(root):
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if filename.endswith('.mp4'):
                yield Path(dirpath) / filename

def unset_clock_files(files):
    """The converted files of events recorded before the camera's clock was set
    (#73). Their times count from the camera's power-up, not real days, so they
    aren't merged."""
    return [f for f in files if (m := FILENAME_RE.search(str(f)))
            and not media_format.clock_was_set(int(m.group(1)))]


def unset_clock_day(day):
    """Whether a day folder ('YYYY/MM/DD') has a date only a camera whose clock
    wasn't set gives its events, such as 1970/01/01 or 2106/02/07 (#73)."""
    return not 2000 <= int(day[:4]) < 2100


UNSET_CLOCK_MERGES = ("holds merged files an earlier version made from events recorded before the "
                      "camera's clock was set; they mix events whose real time is unknown and can be deleted")


# Group files by day
def group_files_by_day(files):
    # An event converted as one file (<event>.mp4) supersedes per-segment files left
    # in its <event>/ folder by older versions; merging both would repeat footage
    events = {f.stem for f in files if FILENAME_RE.search(str(f)) and f.stem.count('_') == 1}
    groups = {}
    for f in files:
        m = FILENAME_RE.search(str(f))
        if not m or f.parent.name in events or not media_format.clock_was_set(int(m.group(1))):
            continue
        timestamp = m.group(1)
        dt = datetime.fromtimestamp(int(timestamp))
        day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        key = day_start
        groups.setdefault(key, []).append((dt, f))
    # Sort files in each group by time
    for k in groups:
        groups[k].sort()
    return groups

def video_end(path):
    """When a file's video ends (its start time plus its length) in seconds, or
    None if ffprobe isn't available or can't read it. Usually the video starts
    at 0; when the first frame is placed later, the file still runs until then
    plus its length."""
    if not FFPROBE_PATH:
        return None
    result = subprocess.run([FFPROBE_PATH, '-v', 'error', '-select_streams', 'v:0',
                             '-show_entries', 'stream=start_time,duration', '-of', 'csv=p=0', str(path)],
                            capture_output=True, text=True, encoding='utf-8', errors='replace')
    try:
        start, length = (float(x) for x in result.stdout.strip().split(','))
        return max(start, 0) + length
    except ValueError:
        return None


def merge_group(period_start, files, summary, record, split_idx=None, total_splits=None):
    out_dir = MERGED_ROOT / period_start.strftime('%Y/%m/%d')
    out_dir.mkdir(parents=True, exist_ok=True)
    start_str = period_start.strftime('%Y%m%d_%H%M')
    end_dt = period_start + timedelta(hours=24 // (total_splits or 1))
    end_str = end_dt.strftime('%Y%m%d_%H%M')
    if split_idx is not None and total_splits is not None:
        out_name = f"merged_{start_str}_{end_str}_part{split_idx+1}of{total_splits}.mp4"
    else:
        out_name = f"merged_{start_str}_{end_str}.mp4"
    out_path = out_dir / out_name
    # Write to a temp file and rename on success, so a failed merge never leaves
    # a broken file that looks like a finished one
    tmp_path = out_dir / (out_name + '.part')
    # Create filelist.txt for ffmpeg concat
    filelist_path = out_dir / f"filelist_{start_str}.txt"
    # FFmpeg reads the list as UTF-8, and a quote inside a quoted path must be
    # written as '\''
    with open(filelist_path, 'w', encoding='utf-8') as f:
        for _, file in files:
            escaped = str(file.resolve()).replace("'", "'\\''")
            # inpoint 0: start each file at its own time zero. Without it, the AAC
            # encoder's start padding (before zero) delays the video at each join
            f.write(f"file '{escaped}'\ninpoint 0\n")
            # End each file exactly where its video ends. AAC audio comes in blocks
            # and the video's last frame has an estimated length, so the two rarely
            # end together; left to itself, FFmpeg starts the next file after one
            # or the other depending on the version, which adds up over many files
            end = video_end(file)
            if end:
                f.write(f"outpoint {end:.6f}\nduration {end:.6f}\n")
    # Copy the video; re-encode the audio once, so the small padding at the start
    # of each segment's AAC track doesn't add up into drift over a long merge
    cmd = [FFMPEG_PATH, '-y', '-f', 'concat', '-safe', '0', '-i', str(filelist_path), '-c:v', 'copy', '-c:a', 'aac', '-f', 'mp4', str(tmp_path)]
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
    sources = [source_name(f) for _, f in files]
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
        return fail(f"couldn't replace the earlier file: {e}")
    record[out_name] = sources   # written with the day's other changes
    success_msg = f"Merged {len(files)} files -> {out_path}"
    print(success_msg)
    summary.append(f"SUCCESS: {success_msg}")
    return out_path


def source_name(path):
    """How the contents record names a converted file: its path in the output
    folder, with forward slashes on every system."""
    return Path(os.path.relpath(path, OUTPUT_ROOT)).as_posix()


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


def remove_stale_merges(day_dir, written, summary, day_files, record):
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
    current = {source_name(f) for f in day_files}
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

def main():
    # Paths can contain characters the console's code page can't encode (e.g. the
    # Windows default when output is piped to the GUI), so always write UTF-8.
    # The streams are None when there's no console at all (pythonw).
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description="Merge .mp4 segments into longer clips per group and time period.")
    parser.add_argument('--output-dir', type=str, default='output', help='Output directory containing .mp4 files to merge (default: output)')
    parser.add_argument('--splits-per-day', type=int, default=1, help='Number of merged .mp4 files to create per day per group (default: 1, i.e. all segments merged into one file per day).')
    args = parser.parse_args()

    # Update global paths based on arguments
    global OUTPUT_ROOT, MERGED_ROOT, SUMMARY_REPORT
    OUTPUT_ROOT = Path(args.output_dir)
    MERGED_ROOT = OUTPUT_ROOT / 'merged'
    SUMMARY_REPORT = MERGED_ROOT / 'merge_summary.txt'

    mp4_files = list(find_mp4_files(OUTPUT_ROOT))
    groups = group_files_by_day(mp4_files)
    summary = []
    failed = 0
    def warn(msg):
        print(f"WARNING: {msg}")
        summary.append(f"WARNING: {msg}")

    unset = sorted(unset_clock_files(mp4_files))
    if unset:
        events = {FILENAME_RE.search(str(f)).group(1) for f in unset}
        warn(f"{len(events)} events weren't merged: they were recorded before the camera's "
             f"clock was set, so their real time is unknown (e.g. {unset[0]})")
    # Earlier versions merged such events into a day of their own; say so rather
    # than delete it, as the record that would show what it holds doesn't exist
    for day_dir in sorted(MERGED_ROOT.glob('*/*/*')):
        if (day_dir.is_dir() and unset_clock_day(day_dir.relative_to(MERGED_ROOT).as_posix())
                and any(MERGED_NAME.fullmatch(f.name) for f in day_dir.iterdir())):
            warn(f"{day_dir} {UNSET_CLOCK_MERGES}")
    for day_start, files in groups.items():
        n = max(1, args.splits_per_day)
        total = len(files)
        split_size = (total + n - 1) // n  # ceil division
        day_dir = MERGED_ROOT / day_start.strftime('%Y/%m/%d')
        record = read_contents(day_dir)
        written, day_failed = set(), False
        for i in range(n):
            split_files = files[i*split_size:(i+1)*split_size]
            if not split_files:
                continue
            split_period_start = day_start + timedelta(hours=(i * 24 // n))
            out_path = merge_group(split_period_start, split_files, summary, record, split_idx=i, total_splits=n)
            if out_path:
                written.add(out_path)
            else:
                failed += 1
                day_failed = True
        if not day_failed:   # keep older files if this day's merge didn't complete
            remove_stale_merges(day_dir, written, summary, [f for _, f in files], record)
        if day_dir.is_dir():
            write_contents(day_dir, record, summary)
    # Write summary report
    MERGED_ROOT.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_REPORT, 'w', encoding='utf-8') as f:
        for line in summary:
            f.write(line + '\n')
    print(f"Summary report written to {SUMMARY_REPORT}")

    return 1 if failed else 0

if __name__ == '__main__':
    sys.exit(main())
