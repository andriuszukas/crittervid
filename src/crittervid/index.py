"""The event index of a merged file (decision record 0007): one row per event,
in the order they play, saying where each starts in the merged file and when it
really started.

The index is a public document: merged_<...>.events.csv next to its merged file,
described by docs/schemas/event-index.json (a Frictionless Table Schema, as
Camtrap DP uses). This module only decides and renders: it takes the facts as
arguments and touches no files, FFmpeg or clock, so the same inputs always give
the same index. It imports only the standard library (a test checks that).
"""
import csv
import io
from datetime import datetime

SCHEMA_VERSION = 1
FIELDS = ('eventStart', 'offsetSeconds', 'durationSeconds', 'filePath', 'shiftSeconds')
SUFFIX = '.events.csv'


class IndexFormatError(ValueError):
    """Text that isn't an event index this version reads."""


def path_for(merged):
    """The index of a merged file (a pathlib path): merged_<...>.events.csv."""
    return merged.with_name(merged.stem + SUFFIX)


def build(events, lengths, lead_in):
    """The index rows of a merged file.

    events: [(start, file path, shift)] in the order they play: when each file's
    footage really started (an aware datetime), the converted file relative to
    the output folder, and the seconds added to its camera time.
    lengths: how long each file plays in the merged file, in seconds: where its
    video ends (its video's start plus its length), as the merge cuts it; None
    if not known.
    lead_in: when the first file's video starts (the merged file starts at its
    first frame).

    Returns [{field: value}]. If any length isn't known, no position is: every
    offsetSeconds and durationSeconds is None."""
    known = all(length is not None for length in lengths)
    rows, position = [], 0.0
    for i, (start, path, shift) in enumerate(events):
        offset = duration = None
        if known:
            duration = lengths[i] - (lead_in if i == 0 else 0)
            offset, position = position, position + duration
        rows.append({'eventStart': start, 'offsetSeconds': offset, 'durationSeconds': duration,
                     'filePath': path, 'shiftSeconds': shift})
    return rows


def describes(rows, events, lengths_known):
    """Whether index rows describe these events (as build takes them) as build
    would now: the same files in order, with the same start times (and UTC
    offsets, so a changed time zone counts) and shifts, and with positions when
    the lengths are known."""
    return ([(r['eventStart'].isoformat(), r['filePath'], r['shiftSeconds']) for r in rows]
            == [(start.isoformat(), path, shift) for start, path, shift in events]
            and not (lengths_known and any(r['offsetSeconds'] is None for r in rows)))


# Positions and lengths are written to the millisecond, so a position can differ
# from the previous one plus its length by up to two half-milliseconds
ROUNDING_S = 0.0015


def misplaced(rows):
    """The first row whose position isn't where the row before it ends (to the
    rounding of the written values), or None. Each row is compared with the one
    before, so rounding doesn't add up over a long index."""
    for before, row in zip(rows, rows[1:]):
        if (row['offsetSeconds'] is None or before['offsetSeconds'] is None
                or abs(row['offsetSeconds'] - before['offsetSeconds'] - before['durationSeconds']) > ROUNDING_S):
            return row
    if rows and rows[0]['offsetSeconds'] not in (None, 0):
        return rows[0]
    return None


def _seconds(value):
    return '' if value is None else f'{value:.3f}'


def to_csv(rows):
    """The index as CSV text, with a header row; unknown values are empty."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(FIELDS)
    for row in rows:
        writer.writerow([row['eventStart'].isoformat(), _seconds(row['offsetSeconds']),
                         _seconds(row['durationSeconds']), row['filePath'], row['shiftSeconds']])
    return out.getvalue()


def has_current_header(text):
    """Whether CSV text starts with this version's header."""
    return text.split('\n', 1)[0].rstrip('\r') == ','.join(FIELDS)


def read_csv(text):
    """The rows of an index's CSV text, as build returns them; IndexFormatError
    if it isn't one this version reads."""
    if not has_current_header(text):
        raise IndexFormatError(f"its columns aren't {', '.join(FIELDS)}")
    rows = []
    for number, values in enumerate(csv.reader(io.StringIO(text)), 1):
        if number == 1:
            continue
        try:
            start, offset, duration, path, shift = values
            start = datetime.fromisoformat(start)
            if start.tzinfo is None:
                raise ValueError('eventStart has no UTC offset')
            rows.append({'eventStart': start,
                         'offsetSeconds': float(offset) if offset else None,
                         'durationSeconds': float(duration) if duration else None,
                         'filePath': path, 'shiftSeconds': int(shift)})
        except ValueError as e:
            raise IndexFormatError(f'row {number} can\'t be read ({e})') from None
    return rows
