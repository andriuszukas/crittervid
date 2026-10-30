"""Deployment details (decision record 0005): the camera's time zone and clock
offset, saved in <output>/deployment.json, and days grouped in that time zone."""
import json
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from crittervid import names

UTC = ZoneInfo('UTC')
LONDON = ZoneInfo('Europe/London')   # clocks forward on 2026-03-29, back on 2026-10-25
KIRITIMATI = 'Pacific/Kiritimati'    # UTC+14: the fixture's afternoon event falls on the next day there


def merged_names(ws):
    return [f.name for f in ws.files('merged_*.mp4')]


def saved(ws):
    return json.loads((ws.root / 'output' / 'deployment.json').read_text(encoding='utf-8'))


# --- Days and parts of days ---

@pytest.mark.parametrize('tz, day, hours', [(UTC, date(2026, 10, 1), 24),
                                            (LONDON, date(2026, 3, 29), 23),
                                            (LONDON, date(2026, 10, 25), 25)])
@pytest.mark.parametrize('n', [1, 2, 5, 7, 24, 1440])
def test_split_windows_cover_the_day_without_gaps(tz, day, hours, n):
    start = names.midnight(day, tz)
    windows = names.split_day(start, [], n, tz)

    assert [k for k, _, _, _ in windows] == list(range(1, n + 1))
    assert windows[0][1] == start and windows[-1][2] == names.midnight(day + timedelta(days=1), tz)
    assert windows[-1][2].timestamp() - start.timestamp() == hours * 3600   # the day's real length
    assert all(a[2].timestamp() == b[1].timestamp() for a, b in zip(windows, windows[1:]))
    lengths = [round(end.timestamp() - begin.timestamp()) // 60 for _, begin, end, _ in windows]
    assert max(lengths) - min(lengths) <= 1   # equal, to the minute
    # (a 23-hour day has fewer minutes than 1440 parts: some windows are empty, so hold no events)
    assert min(lengths) >= (1 if n <= hours * 60 else 0)


def test_names_in_the_hour_repeated_when_the_clocks_go_back_say_which_it_is():
    start = names.midnight(date(2026, 10, 25), LONDON)
    windows = names.split_day(start, [], 50, LONDON)   # 30 minutes each
    made = [names.part_name(begin, end, k, 50) for k, begin, end, _ in windows]

    assert made[2:6] == ['merged_20261025_0100+0100_20261025_0130+0100_part3of50.mp4',
                         'merged_20261025_0130+0100_20261025_0100+0000_part4of50.mp4',
                         'merged_20261025_0100+0000_20261025_0130+0000_part5of50.mp4',
                         'merged_20261025_0130+0000_20261025_0200_part6of50.mp4']
    assert made[0] == 'merged_20261025_0000_20261025_0030_part1of50.mp4'
    assert len(set(made)) == 50 and all(names.MERGED_NAME.fullmatch(name) for name in made)


def test_days_are_the_cameras_local_dates(tmp_path):
    morning, afternoon = tmp_path / '1759300000_0010.mp4', tmp_path / '1759330000_0010.mp4'   # 06:26, 14:46 UTC

    groups = names.group_files_by_day([afternoon, morning], ZoneInfo(KIRITIMATI))

    assert {day.date(): [f for _, f in files] for day, files in groups.items()} == {
        date(2025, 10, 1): [morning], date(2025, 10, 2): [afternoon]}


def test_events_are_ordered_by_instant_in_the_repeated_hour(tmp_path):
    # Folders ordered against the times, so a sort by clock time (then path) fails
    first = tmp_path / 'b' / '1792888200_0010.mp4'    # 01:30 BST
    second = tmp_path / 'a' / '1792891800_0010.mp4'   # 01:30 GMT, an hour later

    [(_, files)] = names.group_files_by_day([second, first], LONDON).items()

    assert [f for _, f in files] == [first, second]


def test_the_clock_offset_moves_only_events_whose_clock_was_set(tmp_path):
    late = tmp_path / '1759359000_0010.mp4'     # 22:50 UTC
    unset = tmp_path / '0000000100_0010.mp4'    # a camera whose clock wasn't set

    groups = names.group_files_by_day([late, unset], UTC, offset=7200)

    assert {day.date(): [(start.hour, f) for start, f in files] for day, files in groups.items()} == {
        date(2025, 10, 2): [(0, late)]}
    assert names.event_time(100, UTC, 1_800_000_000).timestamp() == 100   # still unset


# --- Saved with the outputs ---

def test_the_time_zone_is_saved_and_later_steps_use_it(ws):
    ws.add_media('0001.media', b'morning')
    ws.add_afternoon_media('0001.media', b'afternoon')
    ws.camera_timezone = KIRITIMATI

    result = ws.run('convert_media.py', '--accel', 'cpu')

    assert result.returncode == 0, result.stdout
    assert 'Saved the time zone in' in result.stdout
    assert saved(ws)['schema'] == 1 and saved(ws)['timezone'] == KIRITIMATI and 'timezone' in saved(ws)['set']
    ws.camera_timezone = None   # not given again
    result = ws.run('merge_videos.py')
    assert result.returncode == 0, result.stdout
    assert f'Time zone: {KIRITIMATI} (deployment.json)' in result.stdout
    assert merged_names(ws) == ['merged_20251001_0000_20251002_0000_part1of1.mp4',
                                'merged_20251002_0000_20251003_0000_part1of1.mp4']
    result = ws.run('validate_output.py')
    assert result.returncode == 0, result.stdout
    assert 'WARNING' not in result.stdout


def test_a_changed_time_zone_is_saved_with_a_warning(ws):
    ws.add_media('0001.media', b'morning')
    ws.camera_timezone = KIRITIMATI
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    ws.camera_timezone = 'UTC'

    result = ws.run('merge_videos.py')

    assert f'WARNING: the time zone changed from {KIRITIMATI} to UTC' in result.stdout
    assert 'days are regrouped' in result.stdout
    assert saved(ws)['timezone'] == 'UTC'


def test_validation_alone_doesnt_save_what_its_given(ws):
    ws.add_media('0001.media', b'morning')
    assert ws.run('process_all.py', '--accel', 'cpu').returncode == 0   # saves UTC
    ws.camera_timezone = KIRITIMATI

    result = ws.run('validate_output.py')

    assert f'Time zone: {KIRITIMATI} (option, not saved)' in result.stdout
    assert saved(ws)['timezone'] == 'UTC'


def test_a_dry_run_saves_nothing(ws):
    ws.add_media('0001.media', b'morning')

    result = ws.run('process_all.py', '--dry-run')

    assert result.returncode == 0, result.stdout
    assert '--timezone UTC' in result.stdout   # forwarded to each step
    assert 'Time zone: UTC (option, not saved)' in result.stdout
    assert not (ws.root / 'output' / 'deployment.json').exists()


def test_site_and_camera_are_saved(ws):
    ws.add_media('0001.media', b'morning')

    result = ws.run('process_all.py', '--accel', 'cpu', '--site', 'Pond, north end', '--camera', 'Cam 2')

    assert result.returncode == 0, result.stdout
    assert saved(ws)['site'] == 'Pond, north end' and saved(ws)['camera'] == 'Cam 2'


def test_without_a_time_zone_the_computers_is_used_with_one_warning(ws):
    ws.add_media('0001.media', b'morning')
    ws.camera_timezone = None

    result = ws.run('process_all.py', '--accel', 'cpu')

    assert result.returncode == 0, result.stdout
    assert result.stdout.count('no time zone set: days are grouped in this computer') == 1
    assert not (ws.root / 'output' / 'deployment.json').exists()
    assert merged_names(ws) == ['merged_20251001_0000_20251002_0000_part1of1.mp4']   # TZ is UTC


def test_an_unknown_time_zone_stops_the_step_before_it_converts_anything(ws):
    ws.add_media('0001.media', b'morning')
    ws.camera_timezone = 'Mars/Olympus_Mons'

    result = ws.run('process_all.py', '--accel', 'cpu')

    assert result.returncode == 2, result.stdout
    assert "ERROR: unknown time zone 'Mars/Olympus_Mons'" in result.stdout
    assert ws.files('*.mp4') == [] and not (ws.root / 'output' / 'deployment.json').exists()


def test_a_time_zone_name_must_be_exact():
    from crittervid import deployment
    for name in ('Europe/London ', 'europe/london', '', '../UTC'):
        with pytest.raises(deployment.Invalid):
            deployment.zone(name)


def test_deployment_details_that_cant_be_saved_stop_the_run(ws):
    ws.add_media('0001.media', b'morning')
    (ws.root / 'output').mkdir()
    (ws.root / 'output' / 'deployment.json').mkdir()   # a folder where the file goes

    result = ws.run('convert_media.py', '--accel', 'cpu')

    assert result.returncode == 2, result.stdout
    assert "ERROR: " in result.stdout and 'deployment.json' in result.stdout
    assert 'Traceback' not in result.stdout + result.stderr
    assert list((ws.root / 'output').glob('*.tmp')) == []


@pytest.mark.parametrize('content, problem', [
    ('{not json', "can't be read"),
    ('[]', "isn't a deployment file"),
    ('{"timezone": "UTC"}', 'has no schema number'),
    ('{"schema": 2}', 'written by a newer version'),
    ('{"schema": 1, "timezone": "Mars/Olympus_Mons"}', "unknown time zone 'Mars/Olympus_Mons'"),
    ('{"schema": 1, "clock_offset": "75"}', 'must be a whole number of seconds'),
    ('{"schema": 1, "site": 3}', 'the site must be text'),
])
def test_a_broken_deployment_file_stops_the_run(ws, content, problem):
    ws.add_media('0001.media', b'morning')
    (ws.root / 'output').mkdir()
    (ws.root / 'output' / 'deployment.json').write_text(content, encoding='utf-8')
    ws.camera_timezone = None

    result = ws.run('process_all.py', '--accel', 'cpu')

    assert result.returncode == 2, result.stdout
    assert problem in result.stdout and 'deployment.json' in result.stdout
    assert ws.files('*.mp4') == []
    assert 'grouped in this computer' not in result.stdout   # it doesn't fall back to it


# --- Clock offset ---

def test_the_clock_offset_moves_events_into_the_day_it_gives(ws):
    ws.add_afternoon_media('0001.media', b'afternoon')   # 14:46 UTC

    result = ws.run('process_all.py', '--accel', 'cpu', '--clock-offset', '36000')   # 10 h: 00:46 next day

    assert result.returncode == 0, result.stdout
    assert 'Clock offset: +36000 s (option)' in result.stdout
    assert merged_names(ws) == ['merged_20251002_0000_20251003_0000_part1of1.mp4']
    assert 'WARNING' not in result.stdout   # the card check uses the camera's own time
    assert saved(ws)['clock_offset'] == 36000


# --- The check against the card ---

def test_conversion_warns_when_the_time_zone_puts_events_on_other_days_than_the_card(ws):
    ws.add_media('0001.media', b'morning')
    ws.add_afternoon_media('0001.media', b'afternoon')
    unset = ws.root / 'input' / 'DCIM' / '2106' / '02' / '07' / '0000000100_0001'   # never checked
    unset.mkdir(parents=True)
    (unset / '0001.media').write_bytes(b'unset')
    ws.camera_timezone = KIRITIMATI

    result = ws.run('convert_media.py', '--accel', 'cpu')

    assert f'WARNING: 1 of 2 events fall on a different day in {KIRITIMATI} than on the card' in result.stdout
    ws.camera_timezone = 'UTC'   # the right one
    result = ws.run('convert_media.py', '--accel', 'cpu')
    assert 'than on the card' not in result.stdout


# --- Daylight saving, end to end ---

def test_a_day_when_the_clocks_go_back_merges_and_validates(ws):
    day = ws.root / 'output' / '2026' / '10' / '25'
    day.mkdir(parents=True)
    (day / '1792888200_0010.mp4').write_bytes(b'OKa')   # 01:30 BST
    (day / '1792891800_0010.mp4').write_bytes(b'OKb')   # 01:30 GMT
    ws.camera_timezone = 'Europe/London'

    assert ws.run('merge_videos.py', '--splits-per-day', '50').returncode == 0

    assert merged_names(ws) == ['merged_20261025_0130+0000_20261025_0200_part6of50.mp4',
                                'merged_20261025_0130+0100_20261025_0100+0000_part4of50.mp4']
    result = ws.run('validate_output.py')
    assert result.returncode == 0, result.stdout
    assert 'WARNING' not in result.stdout and 'ERROR' not in result.stdout


# --- The clock offset in merged files, doctor and the summary (PR 2) ---

def creation_times(ws):
    """The creation_time each fake FFmpeg call was given, by its output's name."""
    times = {}
    for call in ws.ffmpeg_calls():
        if 'creation_time' in ' '.join(call):
            stamp = call[call.index('-metadata') + 1].split('=', 1)[1]
            times[Path(call[-1]).name.removesuffix('.part')] = stamp
    return times


def test_merged_files_get_the_corrected_time_and_converted_files_the_cameras(ws):
    ws.env['FAKE_FFMPEG_LOG'] = str(ws.root / 'calls')
    ws.add_media('0001.media', b'morning')   # 06:26:41 UTC by the camera's clock: 1 s into its event

    result = ws.run('process_all.py', '--accel', 'cpu', '--clock-offset', '-40')

    assert result.returncode == 0, result.stdout
    times = creation_times(ws)
    assert times['1759300000_0001_0001.mp4'] == '2025-10-01T06:26:41.000000Z'   # as named
    assert times['merged_20251001_0000_20251002_0000_part1of1.mp4'] == '2025-10-01T06:26:01.000000Z'


def test_a_changed_clock_offset_merges_again(ws):
    ws.add_media('0001.media', b'morning')
    assert ws.run('process_all.py', '--accel', 'cpu').returncode == 0
    assert 'SKIPPED' in ws.run('merge_videos.py').stdout   # up to date

    result = ws.run('merge_videos.py', '--clock-offset', '60')

    assert result.returncode == 0, result.stdout
    assert 'SKIPPED' not in result.stdout and 'Merged 1 files' in result.stdout
    [contents] = ws.files('merged_contents.json')
    assert json.loads(contents.read_text(encoding='utf-8'))['#clock_offsets'] == {
        'merged_20251001_0000_20251002_0000_part1of1.mp4': 60}
    assert 'SKIPPED' in ws.run('merge_videos.py').stdout   # made with the saved offset now
    result = ws.run('validate_output.py')
    assert result.returncode == 0 and 'WARNING' not in result.stdout, result.stdout


def test_a_day_left_without_events_after_a_change_is_reported_not_deleted(ws):
    ws.add_afternoon_media('0001.media', b'afternoon')   # 14:46 UTC: 1 October, or 2 October at UTC+14
    assert ws.run('process_all.py', '--accel', 'cpu', '--no-validate').returncode == 0
    ws.camera_timezone = KIRITIMATI

    result = ws.run('merge_videos.py')

    assert result.returncode == 0, result.stdout
    assert 'holds merged files for a day that has no events now' in result.stdout
    assert merged_names(ws) == ['merged_20251001_0000_20251002_0000_part1of1.mp4',
                                'merged_20251002_0000_20251003_0000_part1of1.mp4']
    assert 'no events now' in (ws.root / 'output' / 'merged' / 'merge_summary.txt').read_text(encoding='utf-8')


def test_a_day_whose_converted_files_were_deleted_isnt_reported_as_regrouped(ws):
    ws.add_media('0001.media', b'morning')
    assert ws.run('process_all.py', '--accel', 'cpu', '--no-validate').returncode == 0
    ws.converted('0001.media').unlink()   # to save space, keeping the merged day

    result = ws.run('merge_videos.py')

    assert 'no events now' not in result.stdout


def test_doctor_shows_the_saved_time_zone(ws):
    ws.add_media('0001.media', b'morning')
    assert ws.run('convert_media.py', '--accel', 'cpu', '--clock-offset', '75').returncode == 0

    result = ws.run_crittervid('doctor')

    assert 'OK: time zone UTC, clock offset +75 s (saved in ' in result.stdout


def test_doctor_warns_when_no_time_zone_is_saved_and_fails_on_a_broken_file(ws):
    result = ws.run_crittervid('doctor')
    assert "WARNING: no time zone saved for output: days will be grouped in this computer's" in result.stdout

    (ws.root / 'output').mkdir()
    (ws.root / 'output' / 'deployment.json').write_text('{not json', encoding='utf-8')
    result = ws.run_crittervid('doctor')
    assert result.returncode == 1 and "ERROR: " in result.stdout and "can't be read" in result.stdout


def test_the_summary_and_the_run_log_show_the_time_zone_in_use(ws):
    ws.add_media('0001.media', b'morning')

    result = ws.run('process_all.py', '--accel', 'cpu', '--clock-offset', '75')

    assert 'Time zone: UTC; clock offset +75 s' in result.stdout.split('=== Summary ===')[1]
    [run_log] = ws.files('*.jsonl')
    records = [json.loads(line) for line in run_log.read_text(encoding='utf-8').splitlines()]
    [used] = [r for r in records if r['message'] == 'Deployment details in use']
    assert used['settings'] == {'timezone': 'UTC', 'clock_offset': 75, 'site': None, 'camera': None}


def test_a_merge_stopped_while_the_offset_changes_merges_again_later(ws, fake_ffmpeg_dir, monkeypatch):
    # Killed after the new file replaced the old, before the day's record was
    # written again: the record mustn't say the file was made with the old offset
    import os
    from crittervid import merge
    from crittervid.config import PipelineConfig
    suffix = '.bat' if os.name == 'nt' else ''
    monkeypatch.setattr(merge, 'get_ffmpeg_path', lambda: str(fake_ffmpeg_dir / f'ffmpeg{suffix}'))
    monkeypatch.setattr(merge, 'get_ffprobe_path', lambda: str(fake_ffmpeg_dir / f'ffprobe{suffix}'))
    ws.add_media('0001.media', b'morning')
    assert ws.run('process_all.py', '--accel', 'cpu', '--no-validate').returncode == 0   # offset 0
    [contents] = ws.files('merged_contents.json')
    on_disk = []
    write = merge.write_contents

    def write_and_keep(*args, **kwargs):
        written = write(*args, **kwargs)
        on_disk.append(contents.read_text(encoding='utf-8'))
        return written
    monkeypatch.setattr(merge, 'write_contents', write_and_keep)

    assert merge.run(PipelineConfig(output_dir=ws.root / 'output', clock_offset=60)) == 0
    contents.write_text(on_disk[0], encoding='utf-8')   # as the stop left it
    result = ws.run('merge_videos.py', '--clock-offset', '0')

    assert 'SKIPPED' not in result.stdout and 'Merged 1 files' in result.stdout


def test_a_day_with_a_merged_file_it_has_no_record_of_isnt_reported_as_regrouped(ws):
    ws.add_afternoon_media('0001.media', b'afternoon')
    assert ws.run('process_all.py', '--accel', 'cpu', '--no-validate').returncode == 0
    old_day = ws.root / 'output' / 'merged' / '2025' / '10' / '01'
    (old_day / 'merged_20251001_0000_20251001_1200_part1of2.mp4').write_bytes(b'OKold')   # unrecorded
    ws.camera_timezone = KIRITIMATI

    result = ws.run('merge_videos.py')

    assert 'no events now' not in result.stdout


def test_the_raw_time_decides_whether_the_clock_was_set(tmp_path):
    from crittervid.media_format import CLOCK_SET_FROM
    unset = tmp_path / f'{CLOCK_SET_FROM - 10:010d}_0010.mp4'   # 10 s before the clock counts as set

    assert names.group_files_by_day([unset], UTC, offset=100) == {}
    assert names.event_time(CLOCK_SET_FROM - 10, UTC, 100).timestamp() == CLOCK_SET_FROM - 10
