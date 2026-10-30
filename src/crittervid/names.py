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
# (with a time's UTC offset, such as +0300, when the clocks went back then)
MERGED_NAME = re.compile(r'merged_\d{8}_\d{4}(?:[+-]\d{4})?_\d{8}_\d{4}(?:[+-]\d{4})?(?:_part(\d+)of(\d+))?\.mp4')

def file_start(path):
    """When a converted file's footage starts, in Unix seconds, from its name: the
    event's start, plus the offset for a per-segment file."""
    m = FILENAME_RE.search(str(path))
    offset = path.stem.split('_')[2] if path.stem.count('_') == 2 else '0'
    return int(m.group(1)) + int(offset)


def unset_clock_files(files):
    """The converted files of events recorded before the camera's clock was set
    (#73). Their times count from the camera's power-up, not real days, so they
    aren't merged unless their series is given a real start (find_series)."""
    return [f for f in files if (m := FILENAME_RE.search(str(f)))
            and not media_format.clock_was_set(int(m.group(1)))]


def unset_clock_day(day):
    """Whether a day folder ('YYYY/MM/DD') has a date only a camera whose clock
    wasn't set gives its events, such as 1970/01/01 or 2106/02/07 (#73). Any
    other folder name isn't a day, so it's False."""
    return bool(re.fullmatch(r'\d{4}/\d{2}/\d{2}', day)) and not 2000 <= int(day[:4]) < 2100


UNSET_CLOCK_MERGES = ("holds merged files an earlier version made from events recorded before the "
                      "camera's clock was set; they mix events whose real time is unknown and can be deleted")


def local_time(seconds, tz=None):
    """Unix seconds as an aware time in tz, or in the computer's time zone for None."""
    if tz is None:
        return datetime.fromtimestamp(seconds).astimezone()
    return datetime.fromtimestamp(seconds, tz)


def midnight(day, tz=None):
    """The start of a date (a date or datetime) in tz, or in the computer's time
    zone for None, as an aware time."""
    naive = datetime(day.year, day.month, day.day)
    return naive.astimezone() if tz is None else naive.replace(tzinfo=tz)


def event_name(path):
    """The event a converted file belongs to, <start>_<length>: an event file's
    own name, or a per-segment file's <event>/ folder."""
    return path.stem if path.stem.count('_') == 1 else path.parent.name


def find_series(files):
    """The series of events recorded before the camera's clock was set (decision
    record 0006), among converted files: [(name, [(start, length, event)])], each
    named after its first event. Events are taken in camera-time order, whatever
    folder they're in (a power-up can span a 2106/ and a 1970/ folder where the
    date wraps); a new series starts when an event starts before the previous one
    ended, as a camera that powers up again counts from 0 again."""
    events = set()
    for f in unset_clock_files(files):
        name = event_name(f)
        if re.fullmatch(r'\d{10,}_\d+', name):
            start, length = name.split('_')
            events.add((int(start), int(length), name))
    series, end = [], None
    for start, length, name in sorted(events):
        if end is None or start < end:
            series.append((name, []))
            end = start + length
        series[-1][1].append((start, length, name))
        end = max(end, start + length)
    return series


def series_shifts(files, starts):
    """{event: seconds to add to its camera time} for the events of each series
    with a real start given (starts: {series name: its first event's real start,
    in Unix seconds})."""
    shifts = {}
    for name, events in find_series(files):
        if name in starts:
            shift = starts[name] - events[0][0]
            shifts.update({event: shift for _, _, event in events})
    return shifts


def shift_of(path, offset=0, shifts=None):
    """Seconds added to a converted file's camera time: the clock offset if its
    camera's clock was set, otherwise its series' shift (None without one)."""
    if media_format.clock_was_set(int(FILENAME_RE.search(str(path)).group(1))):
        return offset
    return (shifts or {}).get(event_name(path))


def event_time(seconds, tz=None, offset=0):
    """When an event (or a file of one) whose camera time is seconds started, in
    tz: with the clock offset added, for a camera whose clock was set."""
    if media_format.clock_was_set(seconds):   # decided on the camera's own time
        seconds += offset
    return local_time(seconds, tz)


def group_files_by_day(files, tz=None, offset=0, shifts=None):
    """{day: [(start, file)]} for converted files of events whose clock was set,
    and of series given a real start (shifts, from series_shifts), each day being
    its local midnight in tz (None: the computer's time zone) and each start the
    event's corrected time, both aware; sorted by start."""
    # An event converted as one file (<event>.mp4) supersedes per-segment files left
    # in its <event>/ folder by older versions; merging both would repeat footage
    events = {f.stem for f in files if FILENAME_RE.search(str(f)) and f.stem.count('_') == 1}
    groups = {}
    for f in files:
        m = FILENAME_RE.search(str(f))
        if not m or f.parent.name in events:
            continue
        shift = shift_of(f, offset, shifts)
        if shift is None:   # recorded before the clock was set, in a series without a start
            continue
        dt = local_time(int(m.group(1)) + shift, tz)
        groups.setdefault(midnight(dt, tz), []).append((dt, f))
    # Sort by instant: two times in the hour repeated when the clocks go back
    # would otherwise be ordered by their clock time
    for k in groups:
        groups[k].sort(key=lambda item: (item[0].timestamp(), str(item[1])))
    return groups

MINUTES_PER_DAY = 24 * 60


def split_day(day_start, files, n, tz=None):
    """How --splits-per-day n divides a day (from day_start, a local midnight in
    tz, to the next): into n equal time windows (to the minute) of its real
    length, which is 23 or 25 hours on the days the clocks change. Part k holds
    the files whose event starts in its window, however long the event runs.
    Returns [(k, window start, window end, files)] for k = 1..n, with aware
    times; a window with no events has no files (as an empty one has, when a
    23-hour day is split into more parts than it has minutes)."""
    start = day_start.timestamp()
    end = midnight(day_start.date() + timedelta(days=1), tz).timestamp()
    minutes = round(end - start) // 60
    bounds = [start + 60 * (k * minutes // n) for k in range(n + 1)]
    return [(k + 1, local_time(bounds[k], tz), local_time(bounds[k + 1], tz),
             [(dt, f) for dt, f in files if bounds[k] <= dt.timestamp() < bounds[k + 1]])
            for k in range(n)]


def _name_time(t):
    """A time in a merged file's name: its clock time, plus its UTC offset when
    that clock time happens twice (the hour repeated when the clocks go back).
    In the computer's time zone (a fixed offset per time) that can't be told,
    so the offset is left out; the part number still keeps names apart."""
    other = t.replace(fold=1 - t.fold)
    repeated = t.tzinfo is not None and other.utcoffset() != t.utcoffset()
    return f"{t:%Y%m%d_%H%M}{t:%z}" if repeated else f"{t:%Y%m%d_%H%M}"


def part_name(start, end, k, n):
    """The merged file for part k of n, covering start to end."""
    return f"merged_{_name_time(start)}_{_name_time(end)}_part{k}of{n}.mp4"
