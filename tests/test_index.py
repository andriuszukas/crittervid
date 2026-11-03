"""The event index of each merged file (decision record 0007): the pure core
(crittervid.index), its schema, and the merge and validation using it."""
import ast
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from crittervid import index

REPO = Path(__file__).resolve().parent.parent
UTC = timezone.utc
T0 = datetime(2025, 10, 1, 6, 26, 41, tzinfo=UTC)


def event(seconds, path='2025/10/01/e.mp4', shift=0):
    return T0 + timedelta(seconds=seconds), path, shift


# --- The pure core: plain data in, rows out ---

def test_positions_add_up_the_lengths_before_each_event():
    rows = index.build([event(0, 'a.mp4'), event(600, 'b.mp4'), event(1300, 'c.mp4')], [10.0, 20.5, 5.0], 0)

    assert [(r['offsetSeconds'], r['durationSeconds']) for r in rows] == [(0.0, 10.0), (10.0, 20.5), (30.5, 5.0)]
    assert [r['filePath'] for r in rows] == ['a.mp4', 'b.mp4', 'c.mp4']


def test_the_first_files_lead_in_isnt_in_the_merged_file():
    # The merged file starts at its first frame: the first file's video starts at 0.8 s
    rows = index.build([event(0), event(600)], [10.8, 20.0], 0.8)

    assert [(r['offsetSeconds'], r['durationSeconds']) for r in rows] == [(0.0, 10.0), (10.0, 20.0)]


def test_one_unknown_length_leaves_every_position_unknown():
    rows = index.build([event(0), event(600), event(1200)], [10.0, None, 5.0], 0)

    assert all(r['offsetSeconds'] is None and r['durationSeconds'] is None for r in rows)
    assert [r['eventStart'] for r in rows] == [T0, T0 + timedelta(seconds=600), T0 + timedelta(seconds=1200)]


def test_csv_round_trip_keeps_every_value():
    plus3 = timezone(timedelta(hours=3))
    rows = index.build([(T0.astimezone(plus3), '2025/10/01/a.mp4', 75), event(60, 'b, "odd".mp4', -3600)],
                       [1.25, 2.5], 0)

    text = index.to_csv(rows)

    assert text.splitlines()[0] == 'eventStart,offsetSeconds,durationSeconds,filePath,shiftSeconds'
    assert text.splitlines()[1] == '2025-10-01T09:26:41+03:00,0.000,1.250,2025/10/01/a.mp4,75'
    assert index.read_csv(text) == rows


def test_unknown_positions_are_empty_cells():
    text = index.to_csv(index.build([event(0)], [None], 0))

    assert text.splitlines()[1] == '2025-10-01T06:26:41+00:00,,,2025/10/01/e.mp4,0'
    assert index.read_csv(text)[0]['offsetSeconds'] is None


@pytest.mark.parametrize('text, problem', [
    ('start,offset\n', "columns aren't"),
    ('eventStart,offsetSeconds,durationSeconds,filePath,shiftSeconds\nnot a time,0,1,a.mp4,0\n', "row 2 can't be read"),
    ('eventStart,offsetSeconds,durationSeconds,filePath,shiftSeconds\n2025-10-01T06:00:00,0,1,a.mp4,0\n', 'no UTC offset'),
    ('eventStart,offsetSeconds,durationSeconds,filePath,shiftSeconds\n2025-10-01T06:00:00Z,0,1\n', "row 2 can't be read"),
])
def test_text_that_isnt_an_index_is_refused(text, problem):
    with pytest.raises(index.IndexFormatError, match=problem):
        index.read_csv(text)


def test_an_index_sits_next_to_its_merged_file():
    merged = Path('out/merged/2025/10/01/merged_20251001_0000_20251002_0000_part1of1.mp4')

    assert index.path_for(merged) == merged.with_name('merged_20251001_0000_20251002_0000_part1of1.events.csv')


# --- Contracts the suite enforces ---

def test_the_columns_are_the_published_schemas():
    # The schema in docs/schemas is the public contract; the code must write exactly its fields
    schema = json.loads((REPO / 'docs' / 'schemas' / 'event-index.json').read_text(encoding='utf-8'))

    assert tuple(field['name'] for field in schema['fields']) == index.FIELDS
    assert schema['version'] == str(index.SCHEMA_VERSION)
    assert schema['missingValues'] == ['']


def test_the_index_core_imports_only_the_standard_library():
    # Decisions apart from effects: no FFmpeg, files, GUI or other CritterVid modules
    tree = ast.parse((REPO / 'src' / 'crittervid' / 'index.py').read_text(encoding='utf-8'))
    imported = {alias.name.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    imported |= {(node.module or '').split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    relative = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level]

    assert relative == [], relative   # no CritterVid module, even one named like a standard one
    assert imported <= set(sys.stdlib_module_names) - {'subprocess', 'os', 'shutil', 'time'}, imported


# --- The merge writing indexes ---

def indexes(ws):
    return {f.name: f for f in ws.files('*.events.csv')}


def read(path):
    return index.read_csv(path.read_text(encoding='utf-8'))


def check_header_against_schema(path):
    schema = json.loads((REPO / 'docs' / 'schemas' / 'event-index.json').read_text(encoding='utf-8'))
    assert path.read_text(encoding='utf-8').splitlines()[0] == ','.join(f['name'] for f in schema['fields'])


def test_the_merge_writes_an_index_next_to_each_merged_file(ws):
    ws.add_media('0001.media', b'morning')
    ws.add_afternoon_media('0001.media', b'afternoon')

    result = ws.run('process_all.py', '--accel', 'cpu', '--clock-offset', '60')

    assert result.returncode == 0, result.stdout
    [path] = indexes(ws).values()
    assert path.name == 'merged_20251001_0000_20251002_0000_part1of1.events.csv'
    check_header_against_schema(path)
    rows = read(path)
    assert [r['filePath'] for r in rows] == ['2025/10/01/1759300000_0001/1759300000_0001_0001.mp4',
                                             '2025/10/01/1759330000_0001/1759330000_0001_0001.mp4']
    assert [r['eventStart'].isoformat() for r in rows] == ['2025-10-01T06:27:41+00:00', '2025-10-01T14:47:41+00:00']
    assert [(r['offsetSeconds'], r['durationSeconds'], r['shiftSeconds']) for r in rows] == [
        (0.0, 1.0, 60), (1.0, 1.0, 60)]
    assert 'WARNING' not in result.stdout


def test_an_up_to_date_merged_file_gets_its_missing_index_without_merging_again(ws):
    ws.add_media('0001.media', b'morning')
    assert ws.run('process_all.py', '--accel', 'cpu', '--no-validate').returncode == 0
    [path] = indexes(ws).values()
    path.unlink()   # as from a version without indexes

    result = ws.run('merge_videos.py')

    assert 'SKIPPED' in result.stdout and path.is_file()
    assert len(read(path)) == 1


def test_a_current_index_isnt_written_again(ws):
    ws.add_media('0001.media', b'morning')
    assert ws.run('process_all.py', '--accel', 'cpu', '--no-validate').returncode == 0
    [path] = indexes(ws).values()
    made = path.stat().st_mtime_ns

    assert 'SKIPPED' in ws.run('merge_videos.py').stdout
    assert path.stat().st_mtime_ns == made


def test_an_index_in_an_older_format_is_written_again(ws):
    ws.add_media('0001.media', b'morning')
    assert ws.run('process_all.py', '--accel', 'cpu', '--no-validate').returncode == 0
    [path] = indexes(ws).values()
    path.write_text('start,offset\n', encoding='utf-8')

    ws.run('merge_videos.py')

    check_header_against_schema(path)


def test_removing_a_merged_file_removes_its_index(ws):
    ws.add_media('0001.media', b'morning')
    ws.add_afternoon_media('0001.media', b'afternoon')
    assert ws.run('process_all.py', '--accel', 'cpu', '--no-validate').returncode == 0

    assert ws.run('merge_videos.py', '--splits-per-day', '2').returncode == 0

    assert sorted(indexes(ws)) == ['merged_20251001_0000_20251001_1200_part1of2.events.csv',
                                   'merged_20251001_1200_20251002_0000_part2of2.events.csv']


def test_an_index_whose_merged_file_was_deleted_is_removed(ws):
    ws.add_media('0001.media', b'morning')
    assert ws.run('process_all.py', '--accel', 'cpu', '--no-validate').returncode == 0
    [merged] = ws.files('merged_*.mp4')
    orphan = index.path_for(merged.with_name('merged_20251001_0000_20251001_1200_part1of2.mp4'))
    orphan.write_text(index.to_csv([]), encoding='utf-8')

    ws.run('merge_videos.py')

    assert not orphan.exists() and index.path_for(merged).exists()


def test_without_ffprobe_the_index_has_no_positions(ws, fake_ffmpeg_dir, monkeypatch):
    from crittervid import merge
    from crittervid.config import PipelineConfig
    suffix = '.bat' if os.name == 'nt' else ''
    monkeypatch.setattr(merge, 'get_ffmpeg_path', lambda: str(fake_ffmpeg_dir / f'ffmpeg{suffix}'))
    monkeypatch.setattr(merge, 'get_ffprobe_path', lambda: None)
    ws.add_media('0001.media', b'morning')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0

    assert merge.run(PipelineConfig(output_dir=ws.root / 'output', timezone='UTC')) == 0

    [path] = indexes(ws).values()
    [row] = read(path)
    assert row['offsetSeconds'] is None and row['durationSeconds'] is None
    assert row['eventStart'].isoformat() == '2025-10-01T06:26:41+00:00'
    summary = (ws.root / 'output' / 'merged' / 'merge_summary.txt').read_text(encoding='utf-8')
    assert 'event indexes' in summary and "but not where it is in the file" in summary


# --- Validation reading the index ---

def test_validation_warns_about_a_missing_index(ws):
    ws.add_media('0001.media', b'morning')
    assert ws.run('process_all.py', '--accel', 'cpu', '--no-validate').returncode == 0
    [path] = indexes(ws).values()
    path.unlink()

    result = ws.run('validate_output.py')

    assert result.returncode == 0
    assert f'no event index ({path.name}); the next merge writes it' in result.stdout


@pytest.mark.parametrize('change, problem', [
    (lambda rows: rows[::-1], "doesn't list the merged file's events in order"),
    (lambda rows: [{**rows[0], 'eventStart': rows[0]['eventStart'] + timedelta(hours=1)}, rows[1]], 'started at'),
    (lambda rows: [rows[0], {**rows[1], 'durationSeconds': 5.0}], 'its events add up to 6.00 s'),
])
def test_validation_warns_about_an_index_that_doesnt_fit(ws, change, problem):
    ws.add_media('0001.media', b'morning')
    ws.add_afternoon_media('0001.media', b'afternoon')
    assert ws.run('process_all.py', '--accel', 'cpu', '--no-validate').returncode == 0
    [path] = indexes(ws).values()
    path.write_text(index.to_csv(change(read(path))), encoding='utf-8')

    result = ws.run('validate_output.py')

    assert result.returncode == 0
    assert problem in result.stdout and 'the next merge writes it again' in result.stdout


def test_validation_doesnt_take_an_index_for_an_unknown_file(ws):
    ws.add_media('0001.media', b'morning')
    assert ws.run('process_all.py', '--accel', 'cpu').returncode == 0
    result = ws.run('validate_output.py')

    assert 'WARNING' not in result.stdout, result.stdout


def test_a_changed_time_zone_rewrites_an_index_whose_merged_file_stays(ws):
    ws.add_media('0001.media', b'morning')
    ws.add_afternoon_media('0001.media', b'afternoon')
    assert ws.run('process_all.py', '--accel', 'cpu', '--no-validate').returncode == 0
    ws.camera_timezone = 'Europe/London'   # same day, same contents: the merged file is up to date

    result = ws.run('merge_videos.py')

    assert 'SKIPPED' in result.stdout
    [path] = indexes(ws).values()
    assert [r['eventStart'].isoformat() for r in read(path)] == ['2025-10-01T07:26:41+01:00',
                                                                 '2025-10-01T15:46:41+01:00']
    assert 'WARNING' not in ws.run('validate_output.py').stdout


def test_a_changed_series_start_rewrites_the_index_even_when_the_first_file_keeps_its_time(ws):
    path = ws.root / 'input' / 'DCIM' / '1970' / '01' / '01' / '0000000001_0010' / '0001.media'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'unset')
    ws.add_media('0001.media', b'morning')   # 06:26:41, first in the merged file
    assert ws.run('process_all.py', '--accel', 'cpu', '--no-validate',
                  '--series-start', '0000000001_0010=2025-10-01T08:00').returncode == 0

    result = ws.run('merge_videos.py', '--series-start', '0000000001_0010=2025-10-01T09:00')

    assert 'SKIPPED' in result.stdout
    [path] = indexes(ws).values()
    assert read(path)[1]['eventStart'].isoformat() == '2025-10-01T09:00:01+00:00'


def test_an_index_older_than_its_merged_file_is_written_again(ws):
    ws.add_media('0001.media', b'morning')
    assert ws.run('process_all.py', '--accel', 'cpu', '--no-validate').returncode == 0
    [merged] = ws.files('merged_*.mp4')
    path = index.path_for(merged)
    path.write_text(path.read_text(encoding='utf-8').replace(',1.000,', ',9.000,'), encoding='utf-8')
    later = merged.stat().st_mtime + 10   # as if the merged file was made again after it
    os.utime(path, (later - 20, later - 20))
    os.utime(merged, (later, later))

    ws.run('merge_videos.py')

    assert read(path)[0]['durationSeconds'] == 1.0


def test_the_index_takes_the_first_files_lead_in_from_its_measured_video(ws):
    from crittervid import merge
    from crittervid.config import PipelineConfig
    ws.add_media('0001.media', b'morning')
    ws.add_afternoon_media('0001.media', b'afternoon')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    config = PipelineConfig(output_dir=ws.root / 'output', timezone='UTC')
    files = sorted((None, f) for f in ws.files('1759*_0001_0001.mp4'))
    out_path = ws.root / 'output' / 'merged_test.mp4'

    assert merge.write_index(out_path, files, [(0.8, 10.0), (0.0, 5.0)], config, None)

    rows = read(index.path_for(out_path))
    assert [(r['offsetSeconds'], r['durationSeconds']) for r in rows] == [(0.0, 10.0), (10.0, 5.0)]


def test_an_index_that_cant_be_written_leaves_no_partial_file(ws, caplog):
    from crittervid import merge
    from crittervid.config import PipelineConfig
    ws.add_media('0001.media', b'morning')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    out_path = ws.root / 'output' / 'merged_test.mp4'
    index.path_for(out_path).mkdir()   # a folder where the index goes

    written = merge.write_index(out_path, [(None, ws.files('*_0001_0001.mp4')[0])], [(0.0, 1.0)],
                                PipelineConfig(output_dir=ws.root / 'output', timezone='UTC'), None)

    assert not written
    assert not list((ws.root / 'output').glob('*.part'))
    assert "couldn't write the event index" in caplog.text


def test_validation_warns_about_positions_that_dont_follow_the_lengths(ws):
    ws.add_media('0001.media', b'morning')
    ws.add_afternoon_media('0001.media', b'afternoon')
    assert ws.run('process_all.py', '--accel', 'cpu', '--no-validate').returncode == 0
    [path] = indexes(ws).values()
    rows = read(path)
    rows[1] = {**rows[1], 'offsetSeconds': 0.5}
    path.write_text(index.to_csv(rows), encoding='utf-8')

    result = ws.run('validate_output.py')

    assert "doesn't start where the event before it ends" in result.stdout


def test_positions_of_a_long_index_follow_each_other_after_rounding():
    # Rounding each written value doesn't add up: each row is checked against the one before
    import random
    rng = random.Random(7)
    lengths = [rng.uniform(5, 60) for _ in range(300)]
    rows = index.read_csv(index.to_csv(index.build([event(i * 600) for i in range(300)], lengths, 0)))

    assert index.misplaced(rows) is None
    rows[150] = {**rows[150], 'offsetSeconds': rows[150]['offsetSeconds'] + 0.01}
    assert index.misplaced(rows) is rows[150]


def test_the_first_event_starts_at_the_beginning():
    rows = index.build([event(0), event(600)], [10.0, 5.0], 0)

    assert index.misplaced([{**rows[0], 'offsetSeconds': 1.0}, {**rows[1], 'offsetSeconds': 11.0}]) is not None
