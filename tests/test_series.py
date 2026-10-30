"""Series of events recorded before the camera's clock was set, and their real
starts (decision record 0006)."""
import json

import pytest

from crittervid import names

SERIES = '0000000001_0602'


def add_unset_series(ws):
    """One power-up's two events, back to back: the first in a 2106/ folder and
    the second in a 1970/ one, where the camera's date wrapped."""
    for folder, event in (('2106/02/07', SERIES), ('1970/01/01', '0000000603_0602')):
        path = ws.root / 'input' / 'DCIM' / folder / event / '0001.media'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'unset')


def merged(ws):
    return [f.relative_to(ws.root / 'output' / 'merged').as_posix() for f in ws.files('merged_*.mp4')]


def saved(ws):
    return json.loads((ws.root / 'output' / 'deployment.json').read_text(encoding='utf-8'))


# --- Finding the series ---

def files(tmp_path, *paths):
    return [tmp_path / p for p in paths]


def test_one_power_up_is_one_series_across_folders(tmp_path):
    found = names.find_series(files(tmp_path, '2106/02/07/0000000001_0602.mp4', '1970/01/01/0000000603_0602.mp4',
                                    '2025/10/01/1759300000_0010.mp4'))

    assert [(name, [event for _, _, event in events]) for name, events in found] == [
        (SERIES, [SERIES, '0000000603_0602'])]


def test_a_new_power_up_starts_a_new_series(tmp_path):
    # The second power-up counts from 0 again, so it starts before the first one's events end
    found = names.find_series(files(tmp_path, '1970/01/01/0000000001_0602.mp4', '1970/01/01/0000000003_0010.mp4'))

    assert [name for name, _ in found] == [SERIES, '0000000003_0010']


def test_per_segment_files_count_as_their_event(tmp_path):
    found = names.find_series(files(tmp_path, f'2106/02/07/{SERIES}/{SERIES}_0001.mp4',
                                    f'2106/02/07/{SERIES}/{SERIES}_0002.mp4'))

    assert [(name, len(events)) for name, events in found] == [(SERIES, 1)]


def test_shifts_move_a_series_to_its_real_start(tmp_path):
    paths = files(tmp_path, '2106/02/07/0000000001_0602.mp4', '1970/01/01/0000000603_0602.mp4')

    shifts = names.series_shifts(paths, {SERIES: 1_759_305_600})   # 2025-10-01 08:00 UTC

    assert shifts == {SERIES: 1_759_305_599, '0000000603_0602': 1_759_305_599}
    groups = names.group_files_by_day(paths, shifts=shifts)
    assert [[t.timestamp() for t, _ in fs] for fs in groups.values()] == [[1_759_305_600, 1_759_306_202]]


# --- Listing them, and giving their starts ---

def test_the_merge_lists_each_series_with_the_option_to_date_it(ws):
    add_unset_series(ws)

    result = ws.run('process_all.py', '--accel', 'cpu')

    assert result.returncode == 0, result.stdout
    assert f'  {SERIES}: 2 events, 20 min: --series-start {SERIES}=<when it started>' in result.stdout
    assert merged(ws) == []


def test_a_dated_series_is_merged_into_its_day(ws):
    ws.env['FAKE_FFMPEG_LOG'] = str(ws.root / 'calls')
    add_unset_series(ws)

    result = ws.run('process_all.py', '--accel', 'cpu', '--series-start', f'{SERIES}=2025-10-01T08:00')

    assert result.returncode == 0, result.stdout
    assert merged(ws) == ['2025/10/01/merged_20251001_0000_20251002_0000_part1of1.mp4']
    assert 'WARNING' not in result.stdout
    assert saved(ws)['schema'] == 2 and saved(ws)['series'] == {SERIES: '2025-10-01T08:00:00+00:00'}
    [merge_call] = [c for c in ws.ffmpeg_calls() if 'concat' in c]
    assert 'creation_time=2025-10-01T08:00:01.000000Z' in merge_call   # segment 0001 starts 1 s in
    assert f'Series {SERIES} started at 2025-10-01T08:00:00+00:00 (option)' in result.stdout


def test_a_changed_series_start_merges_again(ws):
    add_unset_series(ws)
    assert ws.run('process_all.py', '--accel', 'cpu', '--series-start', f'{SERIES}=2025-10-01T08:00').returncode == 0

    result = ws.run('merge_videos.py', '--series-start', f'{SERIES}=2025-10-01T09:00Z')

    assert result.returncode == 0, result.stdout
    assert f'WARNING: the start of series {SERIES} changed from 2025-10-01T08:00:00+00:00' in result.stdout
    assert 'SKIPPED' not in result.stdout and 'Merged 2 files' in result.stdout
    assert 'SKIPPED' in ws.run('merge_videos.py').stdout


@pytest.mark.parametrize('timezone, start, problem', [
    (None, '2025-10-01T08:00', 'no time zone is set'),
    ('Europe/London', '2026-03-29T01:30', "doesn't exist in Europe/London"),
    ('UTC', '2099-01-01T00:00', 'in the future or before 2000'),
    ('UTC', '1999-12-31T23:00', 'in the future or before 2000'),
    ('UTC', 'yesterday', "isn't a date and time"),
])
def test_a_start_that_cant_be_used_stops_the_run(ws, timezone, start, problem):
    add_unset_series(ws)
    ws.camera_timezone = timezone

    result = ws.run('process_all.py', '--accel', 'cpu', '--series-start', f'{SERIES}={start}')

    assert result.returncode == 2, result.stdout
    assert problem in result.stdout


def test_only_a_series_of_unset_events_can_be_dated(ws):
    result = ws.run('process_all.py', '--accel', 'cpu', '--series-start', '1759300000_0001=2025-10-01T08:00')

    assert result.returncode == 2 and "isn't the name of a series" in result.stdout


def test_a_local_start_in_the_repeated_hour_is_the_first_of_the_two(ws):
    add_unset_series(ws)
    ws.camera_timezone = 'Europe/London'

    result = ws.run('process_all.py', '--accel', 'cpu', '--no-validate', '--series-start', f'{SERIES}=2025-10-26T01:30')

    assert result.returncode == 0, result.stdout
    assert saved(ws)['series'] == {SERIES: '2025-10-26T01:30:00+01:00'}


def test_a_dated_series_overlapping_other_footage_is_warned_about(ws):
    add_unset_series(ws)
    ws.add_media('0001.media', b'morning')   # 06:26:41 UTC

    result = ws.run('process_all.py', '--accel', 'cpu', '--no-validate', '--series-start', f'{SERIES}=2025-10-01T06:20')

    assert f'series {SERIES}, as dated, overlaps 1 other recordings' in result.stdout


def test_a_start_for_a_series_that_isnt_there_is_kept_with_a_warning(ws):
    ws.add_media('0001.media', b'morning')

    result = ws.run('process_all.py', '--accel', 'cpu', '--no-validate', '--series-start', f'{SERIES}=2025-10-01T08:00')

    assert result.returncode == 0, result.stdout
    assert f'a start is saved for series {SERIES}, but none of the converted files belong to it' in result.stdout
    assert SERIES in saved(ws)['series']


def test_a_file_without_series_keeps_schema_1(ws):
    ws.add_media('0001.media', b'morning')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0

    assert saved(ws)['schema'] == 1 and 'series' not in saved(ws)


@pytest.mark.parametrize('series, problem', [
    ('[]', 'series must be a map'),
    ('{"0000000001_0602": "2025-10-01T08:00"}', 'with its UTC offset'),
    ('{"1759300000_0001": "2025-10-01T08:00:00+00:00"}', "isn't the name of a series"),
])
def test_a_broken_series_in_the_file_stops_the_run(ws, series, problem):
    (ws.root / 'output').mkdir()
    (ws.root / 'output' / 'deployment.json').write_text(f'{{"schema": 2, "series": {series}}}', encoding='utf-8')

    result = ws.run('merge_videos.py')

    assert result.returncode == 2 and problem in result.stdout
