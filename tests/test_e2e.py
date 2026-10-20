"""End to end: a generated SD card in the cameras' layout and record format, run
through the whole pipeline with the real FFmpeg, checking what comes out rather
than just the exit code. Skipped when FFmpeg isn't installed (required on CI).

The card follows Tuya's IPC storage layout, which these cameras use:
DCIM/YYYY/MM/DD/<event start, UTC epoch>_<event length in s>/<offset in s>.media,
each .media file a sequence of timestamped video and audio records
(see docs/decisions/0001-media-format.md and tests/media_fixture.py).
"""
import json
import shutil
import subprocess

import pytest

from media_fixture import utc, write_event

# Events as (UTC start, length in seconds). Times are mid-day UTC, so each event
# falls on the same calendar day for any machine timezone from UTC-6 to UTC+8;
# merging groups by the processing machine's local day (see #8).
EVENTS = [
    (utc(2026, 10, 1, 6, 0), 25),
    (utc(2026, 10, 1, 15, 30), 14),
    (utc(2026, 10, 2, 9, 0), 12),
    (utc(2026, 10, 2, 12, 0), 20),
]
DAY_TOTALS = {(2026, 10, 1): 39, (2026, 10, 2): 32}
TOLERANCE = 0.15   # seconds; the last frame's duration is estimated


@pytest.fixture(scope='module')
def card(tmp_path_factory):
    """The generated DCIM folder, built once per test module."""
    if not (shutil.which('ffmpeg') and shutil.which('ffprobe')):
        return None
    dcim = tmp_path_factory.mktemp('card') / 'DCIM'
    for start, seconds in EVENTS:
        write_event(dcim, start, seconds)
    return dcim


@pytest.fixture
def card_ws(real_ws, card):
    shutil.copytree(card, real_ws.root / 'input' / 'DCIM')
    return real_ws


def streams(path):
    out = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,start_time,duration', '-of', 'json', str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return {s['codec_type']: (float(s['start_time']), float(s['duration'])) for s in json.loads(out)['streams']}


def event_files(ws):
    return [f for f in ws.files('*.mp4') if 'merged' not in f.parts]


def merged_by_day(ws):
    days = {}
    for f in ws.files('merged_*.mp4'):
        # output/merged/YYYY/MM/DD/merged_*.mp4
        day = tuple(int(part) for part in f.parent.relative_to(ws.root / 'output' / 'merged').parts)
        days.setdefault(day, []).append(f)
    return days


def run(ws, *args):
    return ws.run('process_all.py', '--accel', 'cpu', *args)


def test_card_converts_merges_and_validates(card_ws):
    result = run(card_ws)

    assert result.returncode == 0, result.stdout + result.stderr
    assert 'All output videos passed the checks.' in result.stdout
    assert 'WARNING' not in result.stdout
    assert card_ws.files('*.part') == []

    # One file per event, at its true length, with audio alongside the video
    files = {f.stem: f for f in event_files(card_ws)}
    assert len(files) == len(EVENTS)
    for start, seconds in EVENTS:
        s = streams(files[f'{int(start.timestamp())}_{seconds:04d}'])
        assert s['video'][1] == pytest.approx(seconds, abs=TOLERANCE)
        assert s['audio'][1] == pytest.approx(s['video'][1], abs=TOLERANCE)

    # One merged file per day: all of that day's footage, video and audio together
    days = merged_by_day(card_ws)
    assert sorted(days) == sorted(DAY_TOTALS)
    for day, files in days.items():
        [merged] = files
        s = streams(merged)
        assert s['video'][0] == s['audio'][0] == 0
        assert s['video'][1] == pytest.approx(DAY_TOTALS[day], abs=2 * TOLERANCE), day
        assert s['audio'][1] == pytest.approx(s['video'][1], abs=TOLERANCE), day

    assert (card_ws.root / 'output' / 'conversion_summary.txt').is_file()
    assert (card_ws.root / 'output' / 'merged' / 'merge_summary.txt').is_file()


def test_card_rerun_skips_finished_work(card_ws):
    assert run(card_ws).returncode == 0
    converted = {f: f.stat().st_mtime_ns for f in event_files(card_ws)}

    result = run(card_ws)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count('SKIPPED:') == len(EVENTS)
    assert {f: f.stat().st_mtime_ns for f in converted} == converted


def test_card_splits_each_day(card_ws):
    result = run(card_ws, '--splits-per-day', '2')

    assert result.returncode == 0, result.stdout + result.stderr
    days = merged_by_day(card_ws)
    assert sorted(days) == sorted(DAY_TOTALS)
    for day, files in days.items():
        assert sorted(f.name.rsplit('_', 1)[1] for f in files) == ['part1of2.mp4', 'part2of2.mp4']
        total = sum(streams(f)['video'][1] for f in files)
        assert total == pytest.approx(DAY_TOTALS[day], abs=2 * TOLERANCE), day
