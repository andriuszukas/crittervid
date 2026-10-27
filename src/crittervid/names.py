"""Names of the files the pipeline writes, and how converted files are grouped
into days and parts of days: shared by the merge and validation."""
import re
from datetime import datetime, timedelta

from . import media_format

# Regex to extract the event's start time and length from a file name
# Converted files: one per event (<timestamp>_<duration>.mp4) for the cameras'
# record format, or one per segment (<timestamp>_<duration>_<offset>.mp4)
# (the cameras write 4-digit lengths, but any length is accepted)
FILENAME_RE = re.compile(r'(?:^|[\\/])(\d{10,})_(\d+)(?:_\d+)?\.mp4$')
# Names of the files this step writes: merged_<start>_<end>[_part<K>of<N>].mp4
MERGED_NAME = re.compile(r'merged_\d{8}_\d{4}_\d{8}_\d{4}(?:_part(\d+)of(\d+))?\.mp4')

def file_start(path):
    """When a converted file's footage starts, in Unix seconds, from its name: the
    event's start, plus the offset for a per-segment file."""
    m = FILENAME_RE.search(str(path))
    offset = path.stem.split('_')[2] if path.stem.count('_') == 2 else '0'
    return int(m.group(1)) + int(offset)


def unset_clock_files(files):
    """The converted files of events recorded before the camera's clock was set
    (#73). Their times count from the camera's power-up, not real days, so they
    aren't merged."""
    return [f for f in files if (m := FILENAME_RE.search(str(f)))
            and not media_format.clock_was_set(int(m.group(1)))]


def unset_clock_day(day):
    """Whether a day folder ('YYYY/MM/DD') has a date only a camera whose clock
    wasn't set gives its events, such as 1970/01/01 or 2106/02/07 (#73). Any
    other folder name isn't a day, so it's False."""
    return bool(re.fullmatch(r'\d{4}/\d{2}/\d{2}', day)) and not 2000 <= int(day[:4]) < 2100


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

MINUTES_PER_DAY = 24 * 60


def split_day(day_start, files, n):
    """How --splits-per-day n divides a day: into n equal time windows (to the
    minute), part k holding the files whose event starts in its window, however
    long the event runs. Returns [(k, window start, window end, files)] for
    k = 1..n; a window with no events has no files."""
    bounds = [day_start + timedelta(minutes=k * MINUTES_PER_DAY // n) for k in range(n + 1)]
    return [(k + 1, bounds[k], bounds[k + 1], [(dt, f) for dt, f in files if bounds[k] <= dt < bounds[k + 1]])
            for k in range(n)]


def part_name(start, end, k, n):
    """The merged file for part k of n, covering start to end."""
    return f"merged_{start:%Y%m%d_%H%M}_{end:%Y%m%d_%H%M}_part{k}of{n}.mp4"
