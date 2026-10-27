"""Pipeline behaviour, using the fake ffmpeg from fake_ffmpeg.py."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import GROUP_DIR


# --- Conversion: failed output must never look finished ---

def test_failed_conversion_leaves_no_output(ws):
    ws.add_media('0001.media', b'good')
    bad = ws.add_media('0002.media', b'BAD')

    result = ws.run('convert_media.py', '--accel', 'cpu')

    assert ws.converted('0001.media').read_bytes() == b'OKgood'
    assert not ws.converted('0002.media').exists()
    assert ws.files('*.part') == []
    failed_list = (ws.root / 'output' / 'conversion_failed.txt').read_text()
    assert str(bad) in failed_list
    assert result.returncode == 1


def test_failed_file_is_converted_on_next_run(ws):
    ws.add_media('0001.media', b'good')
    ws.add_media('0002.media', b'BAD')
    ws.run('convert_media.py', '--accel', 'cpu')

    ws.add_media('0002.media', b'fixed')
    result = ws.run('convert_media.py', '--accel', 'cpu')

    assert result.returncode == 0
    assert 'SKIPPED' in result.stdout  # 0001 was already done
    assert ws.converted('0002.media').read_bytes() == b'OKfixed'
    assert not (ws.root / 'output' / 'conversion_failed.txt').exists()


def test_copy_mode_copies_the_video_stream(ws):
    ws.add_media('0001.media', b'good')
    ws.env['FAKE_FFMPEG_LOG'] = str(ws.root / 'ffmpeg_calls.jsonl')

    result = ws.run('convert_media.py', '--copy')

    assert result.returncode == 0
    assert '[copy]' in result.stdout
    [args] = ws.ffmpeg_calls()
    assert args[args.index('-c:v') + 1] == 'copy'
    assert '-hwaccel' not in args


@pytest.mark.parametrize(('accel', 'hwaccel', 'encoder'), [
    ('cuda', 'cuda', 'h264_nvenc'),
    ('qsv', 'qsv', 'h264_qsv'),
    ('vaapi', 'vaapi', 'h264_vaapi'),
    ('amf', 'dxva2', 'h264_amf'),
    ('cpu', None, None),
])
def test_accel_options_reach_ffmpeg(ws, accel, hwaccel, encoder):
    ws.add_media('0001.media', b'good')
    ws.env['FAKE_FFMPEG_LOG'] = str(ws.root / 'ffmpeg_calls.jsonl')

    assert ws.run('convert_media.py', '--accel', accel).returncode == 0

    [args] = ws.ffmpeg_calls()
    i = args.index('-i')
    if hwaccel:
        # Decoder option before the input, encoder after it
        assert args[args.index('-hwaccel') + 1] == hwaccel and args.index('-hwaccel') < i
        assert args[args.index('-c:v') + 1] == encoder and args.index('-c:v') > i
    else:
        assert '-hwaccel' not in args and '-c:v' not in args


# --- Merge ---

def test_merge_succeeds(ws):
    ws.add_media('0001.media', b'a')
    ws.add_media('0002.media', b'b')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0

    result = ws.run('merge_videos.py')

    assert result.returncode == 0
    [merged] = ws.files('merged_*.mp4')
    assert merged.read_bytes() == b'OKOKaOKb'
    assert ws.files('*.part') == []


def test_merge_skips_old_segment_files_of_events_converted_whole(ws):
    # Older versions wrote one file per segment into an <event>/ folder; this one
    # writes <event>.mp4. Both may exist after an upgrade: merge only the event file
    event_dir = ws.converted('0001.media').parent
    event_dir.mkdir(parents=True)
    ws.converted('0001.media').write_bytes(b'OKold')
    (event_dir.parent / f'{event_dir.name}.mp4').write_bytes(b'OKnew')

    assert ws.run('merge_videos.py').returncode == 0

    [merged] = ws.files('merged_*.mp4')
    assert merged.read_bytes() == b'OKOKnew'


def test_failed_merge_leaves_no_output(ws):
    ws.add_media('0001.media', b'good')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    # A converted segment the fake ffmpeg can't concatenate
    ws.converted('0002.media').write_bytes(b'BAD')

    result = ws.run('merge_videos.py')

    assert ws.files('merged_*') == []
    assert ws.files('*.part') == []
    assert result.returncode == 1


# --- Validation ---

def test_validate_passes_on_good_output(ws):
    ws.add_media('0001.media', b'good')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0

    assert ws.run('validate_output.py').returncode == 0


def test_validate_fails_on_unplayable_output(ws):
    ws.add_media('0001.media', b'good')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    ws.converted('0001.media').write_bytes(b'corrupt')

    result = ws.run('validate_output.py')

    assert result.returncode == 1
    assert 'unreadable' in result.stdout


def event_file(ws, name, content):
    path = ws.root / 'output' / '2026' / '10' / '01' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_validate_reports_an_event_file_without_audio(ws):
    event_file(ws, '1790834400_0010.mp4', b'OKNOAUDIO')

    result = ws.run('validate_output.py')

    assert result.returncode == 1
    assert 'no audio track' in result.stdout


def test_validate_reports_a_merged_day_shorter_than_its_events(ws):
    # Two events that day, but the merged file only contains one of them
    event_file(ws, '1790834400_0010.mp4', b'OKa')
    event_file(ws, '1790835000_0010.mp4', b'OKb')
    merged = ws.root / 'output' / 'merged' / '2026' / '10' / '01' / 'merged_20261001_0000_20261002_0000_part1of1.mp4'
    merged.parent.mkdir(parents=True)
    merged.write_bytes(b'OKOKa')

    result = ws.run('validate_output.py')

    assert result.returncode == 1
    assert 'merged 2026/10/01 part 1 of 1 is 1.00 s long, but its 2 converted files add up to 2.00 s' in result.stdout


def test_validate_passes_a_complete_pipeline_run_and_checks_merged_days(ws):
    ws.add_media('0001.media', b'good')
    ws.add_media('0002.media', b'more')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0

    result = ws.run('validate_output.py')

    assert result.returncode == 0, result.stdout
    assert 'All output videos passed the checks.' in result.stdout
    assert 'WARNING' not in result.stdout


def test_validate_warns_about_unexpected_names(ws):
    event_file(ws, '1790834400_0010.mp4', b'OKa')
    event_file(ws, 'holiday video.mp4', b'OKb')

    result = ws.run('validate_output.py')

    assert result.returncode == 0
    assert 'holiday video.mp4: not a name the converter writes' in result.stdout


def test_deep_validation_decodes_each_file_once(ws):
    ws.add_media('0001.media', b'good')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0
    ws.env['FAKE_FFMPEG_LOG'] = str(ws.root / 'ffmpeg_calls.jsonl')

    assert ws.run('validate_output.py', '--deep').returncode == 0

    decoded = [args[args.index('-i') + 1] for args in ws.ffmpeg_calls() if args[-1] == '-']
    assert len(decoded) == len(set(decoded)) == 2   # the converted file and the merged file


# --- Full pipeline ---

@pytest.mark.parametrize(('args', 'expected_summaries'), [
    ((), [
        'Conversion summary: output/conversion_summary.txt',
        'Merging summary: output/merged/merge_summary.txt',
    ]),
    (('--output-dir', 'reports'), [
        'Conversion summary: reports/conversion_summary.txt',
        'Merging summary: reports/merged/merge_summary.txt',
    ]),
    (('--output-dir', 'reports with spaces'), [
        'Conversion summary: reports with spaces/conversion_summary.txt',
        'Merging summary: reports with spaces/merged/merge_summary.txt',
    ]),
], ids=['default', 'relative', 'spaces'])
def test_process_all_dry_run_summary_paths(ws, args, expected_summaries):
    result = ws.run('process_all.py', '--dry-run', *args)

    assert result.returncode == 0, result.stdout + result.stderr
    summaries = [line for line in result.stdout.splitlines()
                 if line.startswith(('Conversion summary:', 'Merging summary:'))]
    assert summaries == [line.replace('/', os.sep) for line in expected_summaries]


def test_process_all_succeeds(ws):
    ws.add_media('0001.media', b'good')

    result = ws.run('process_all.py', '--accel', 'cpu')

    assert result.returncode == 0, result.stdout
    assert 'All steps completed successfully.' in result.stdout


def test_process_all_fails_when_a_step_fails(ws):
    ws.add_media('0001.media', b'good')
    ws.add_media('0002.media', b'BAD')

    result = ws.run('process_all.py', '--accel', 'cpu')

    assert result.returncode == 1
    assert 'Some steps failed' in result.stdout


def test_process_all_handles_quotes_and_non_ascii_in_paths(ws):
    # An apostrophe must be escaped in the concat list, and characters outside
    # the Windows default code page must not crash printing or file writes
    out_dir = "out O'Brien ėų 野"
    ws.add_media('0001.media', b'a')
    ws.add_media('0002.media', b'b')

    result = ws.run('process_all.py', '--accel', 'cpu', '--output-dir', out_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    [merged] = (ws.root / out_dir).rglob('merged_*.mp4')
    assert merged.read_bytes() == b'OKOKaOKb'
    assert out_dir in result.stdout


def test_error_output_is_utf8(ws):
    # Errors go to stderr, which must be UTF-8 like stdout so the GUI shows
    # non-ASCII characters in error messages correctly
    result = ws.run('convert_media.py', '--accel', 'ė')

    assert result.returncode != 0
    assert "'ė'" in result.stderr


def test_process_all_output_is_in_order_when_piped(ws):
    # The GUI reads the pipeline through a pipe; step headers must not be held
    # back in a buffer and show up after the output of the steps they introduce
    ws.add_media('0001.media', b'good')

    lines = ws.run('process_all.py', '--accel', 'cpu').stdout.splitlines()

    def index(prefix):
        return next(i for i, line in enumerate(lines) if line.startswith(prefix))

    assert (index('=== Step 1') < index('Found 1 .media')
            < index('=== Step 2') < index('Merged 1 files')
            < index('=== Step 3') < index('Checking ')
            < index('=== Summary'))


# --- Removing merged files left from an earlier split setting ---

def merged_dir(ws):
    [day] = {f.parent for f in ws.files('merged_*.mp4')}
    return day


def test_merge_only_removes_files_it_wrote_itself(ws):
    ws.add_media('0001.media', b'good')
    ws.add_afternoon_media('0002.media', b'more')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0
    day = merged_dir(ws)
    (day / 'merged_my_best_bits.mp4').write_bytes(b'OKmine')

    result = ws.run('merge_videos.py', '--splits-per-day', '2')

    assert result.returncode == 0, result.stdout
    assert sorted(f.name for f in day.glob('*.mp4')) == [
        'merged_20251001_0000_20251001_1200_part1of2.mp4',
        'merged_20251001_1200_20251002_0000_part2of2.mp4',
        'merged_my_best_bits.mp4']


def test_merge_keeps_older_files_holding_more_footage(ws):
    # The converted files of one event were deleted to save space; the earlier
    # merged file is now the only copy of that footage
    ws.add_media('0001.media', b'good')
    ws.add_media('0002.media', b'more')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0
    ws.converted('0002.media').unlink()

    result = ws.run('merge_videos.py', '--splits-per-day', '2')

    assert 'WARNING: kept 1 merged files' in result.stdout and 'are gone' in result.stdout
    assert len(list(merged_dir(ws).glob('merged_*_part1of1.mp4'))) == 1


def test_merge_keeps_older_files_when_an_event_was_replaced(ws):
    # Same number of files (and so the same total length) as before, but one
    # event's converted file is gone: the old merged file is its only copy
    ws.add_media('0001.media', b'good')
    ws.add_media('0002.media', b'more')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0
    gone = ws.converted('0002.media')
    gone.rename(gone.with_name('1759300000_0001_0003.mp4'))

    result = ws.run('merge_videos.py', '--splits-per-day', '2')

    assert result.returncode == 0, result.stdout
    assert 'WARNING: kept 1 merged files' in result.stdout and gone.name in result.stdout
    assert len(list(merged_dir(ws).glob('merged_*_part1of1.mp4'))) == 1


def test_merge_records_what_each_merged_file_holds(ws):
    ws.add_media('0001.media', b'good')
    ws.add_media('0002.media', b'more')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py', '--splits-per-day', '2').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0

    day = merged_dir(ws)
    record = json.loads((day / 'merged_contents.json').read_text(encoding='utf-8'))
    out = ws.root / 'output'
    assert record == {f.name: sorted(Path(os.path.relpath(c, out)).as_posix()
                                     for c in (ws.converted('0001.media'), ws.converted('0002.media')))
                      for f in day.glob('merged_*.mp4')}
    assert len(record) == 1   # the parts of the 2-per-day set were removed and forgotten


def test_merge_keeps_older_files_when_this_merge_failed(ws):
    # The old file holds no more footage than the new parts would, so only the
    # failed merge is a reason to keep it
    ws.add_media('0001.media', b'good')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0
    ws.converted('0002.media').write_bytes(b'BAD')   # the stand-in can't join this

    assert ws.run('merge_videos.py', '--splits-per-day', '2').returncode == 1

    assert len(list(merged_dir(ws).glob('merged_*_part1of1.mp4'))) == 1


@pytest.mark.skipif(os.name != 'nt', reason='only Windows refuses to delete an open file')
def test_merge_carries_on_when_an_old_file_is_open_elsewhere(ws):
    ws.add_media('0001.media', b'good')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0
    [old] = merged_dir(ws).glob('merged_*.mp4')

    with open(old, 'rb'):   # e.g. still open in a video player
        result = ws.run('merge_videos.py', '--splits-per-day', '2')

    assert result.returncode == 0, result.stdout
    assert "WARNING: couldn't remove" in result.stdout
    assert (ws.root / 'output' / 'merged' / 'merge_summary.txt').is_file()


def test_merge_keeps_older_files_it_has_no_record_of(ws):
    # Made by an earlier version, which didn't record what each file holds
    ws.add_media('0001.media', b'good')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0
    [old] = merged_dir(ws).glob('merged_*.mp4')
    (old.parent / 'merged_contents.json').unlink()

    result = ws.run('merge_videos.py', '--splits-per-day', '2')

    assert "WARNING: kept 1 merged files" in result.stdout and "didn't record" in result.stdout
    assert old.exists()


@pytest.mark.parametrize('record', ['not json', '[]', '{"%s": 5}', '{"%s": []}'])
def test_merge_keeps_older_files_when_its_record_is_damaged(ws, record):
    ws.add_media('0001.media', b'good')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0
    [old] = merged_dir(ws).glob('merged_*.mp4')
    (old.parent / 'merged_contents.json').write_text(record.replace('%s', old.name), encoding='utf-8')

    result = ws.run('merge_videos.py', '--splits-per-day', '2')

    assert result.returncode == 0, result.stdout
    assert "WARNING: kept 1 merged files" in result.stdout
    assert old.exists()


def test_process_all_repeats_merge_warnings_in_its_summary(ws):
    ws.add_media('0001.media', b'good')
    assert ws.run('process_all.py', '--accel', 'cpu').returncode == 0
    [old] = merged_dir(ws).glob('merged_*.mp4')
    (old.parent / 'merged_contents.json').unlink()

    result = ws.run('process_all.py', '--accel', 'cpu', '--splits-per-day', '2', '--no-validate')

    summary = result.stdout[result.stdout.index('=== Summary ==='):]
    assert 'Merge WARNING: kept 1 merged files' in summary


def test_process_all_doesnt_repeat_warnings_from_an_earlier_merge(ws):
    ws.add_media('0001.media', b'good')
    report = ws.root / 'output' / 'merged' / 'merge_summary.txt'
    report.parent.mkdir(parents=True)
    report.write_text('WARNING: from an earlier run\n', encoding='utf-8')
    # A file where the day's merged folder should be stops the merge before it
    # writes its report
    (report.parent / '2025' / '10').mkdir(parents=True)
    (report.parent / '2025' / '10' / '01').write_bytes(b'')

    result = ws.run('process_all.py', '--accel', 'cpu', '--no-validate')

    assert result.returncode == 1
    assert 'from an earlier run' not in result.stdout


def test_merge_records_a_file_before_replacing_it(ws):
    # If the run stops after the record is written but before the new file
    # replaces the old one, the record must list at least what the file holds
    ws.add_media('0001.media', b'good')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0
    day = merged_dir(ws)
    [old] = day.glob('merged_*.mp4')
    before = old.read_bytes()
    ws.add_media('0002.media', b'more')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    record = day / 'merged_contents.json'
    record.unlink()
    record.mkdir()   # can't be written now

    result = ws.run('merge_videos.py')

    assert result.returncode == 1
    assert old.read_bytes() == before   # not replaced by a file the record doesn't describe
    assert "couldn't record its contents" in result.stdout
    assert ws.files('*.part') == []


@pytest.mark.skipif(os.name != 'nt', reason='only Windows refuses to replace an open file')
def test_merge_still_knows_what_a_file_it_couldnt_replace_holds(ws):
    ws.add_media('0001.media', b'good')
    ws.add_media('0002.media', b'more')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0
    [old] = merged_dir(ws).glob('merged_*.mp4')
    before = old.read_bytes()
    ws.converted('0001.media').unlink()   # its footage is now only in the old file

    with open(old, 'rb'):   # e.g. still open in a video player
        result = ws.run('merge_videos.py')
    assert result.returncode == 1
    assert "couldn't replace the earlier file" in result.stdout
    assert old.read_bytes() == before
    assert ws.files('*.part') == []

    result = ws.run('merge_videos.py', '--splits-per-day', '2')
    assert 'WARNING: kept 1 merged files' in result.stdout and 'are gone' in result.stdout
    assert old.read_bytes() == before


def test_merge_forgets_events_a_replaced_file_no_longer_holds(ws):
    ws.add_media('0001.media', b'good')
    ws.add_media('0002.media', b'more')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0
    ws.converted('0001.media').unlink()

    assert ws.run('merge_videos.py').returncode == 0

    record = json.loads((merged_dir(ws) / 'merged_contents.json').read_text(encoding='utf-8'))
    assert [len(sources) for sources in record.values()] == [1]



# --- Events recorded before the camera's clock was set (#73) ---

def test_events_recorded_before_the_clock_was_set_are_converted_but_not_merged(ws):
    ws.add_media('0001.media', b'good')
    unset = ['1970/01/01/0000010800_0602', '2106/02/07/0000000001_0602']
    for folder in unset:
        path = ws.root / 'input' / 'DCIM' / folder / '0000.media'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'unset clock')
    (path.parent / '0013.media').write_bytes(b'more')   # two files for one event

    result = ws.run('convert_media.py', '--accel', 'cpu')

    assert result.returncode == 0, result.stdout
    warned = [line for line in result.stdout.splitlines() if "before the camera's clock was set" in line]
    assert len(warned) == 2
    for folder in unset:   # the footage is kept, in the card's own folders
        assert ws.files(f'{Path(folder).name}_0000.mp4')

    # A merged day an earlier version made of them
    merged = ws.root / 'output' / 'merged'
    old = merged / '1970' / '01' / '01' / 'merged_19700101_0000_19700102_0000_part1of1.mp4'
    old.parent.mkdir(parents=True)
    old.write_bytes(b'OKOKOK')

    result = ws.run('merge_videos.py')

    assert result.returncode == 0, result.stdout
    assert "WARNING: 2 events weren't merged" in result.stdout
    assert [f.relative_to(merged).parts[0] for f in ws.files('merged_*.mp4')] == ['1970', '2025']
    assert 'an earlier version made' in result.stdout and old.exists()   # pointed out, not deleted

    result = ws.run('validate_output.py')
    assert result.returncode == 0, result.stdout
    assert 'an earlier version made' in result.stdout and 'no matching converted files' not in result.stdout


def test_merge_and_validate_ignore_folders_that_arent_days(ws):
    ws.add_media('0001.media', b'good')
    merged = ws.root / 'output' / 'merged'
    (merged / 'notes' / 'x' / 'y').mkdir(parents=True)
    (merged / '2025.bak' / '01' / '01').mkdir(parents=True)
    (merged / 'merged_20251001_0000_20251002_0000.mp4').write_bytes(b'OKOK')   # not in a day folder
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0

    result = ws.run('merge_videos.py')
    assert result.returncode == 0, result.stdout + result.stderr

    result = ws.run('validate_output.py')
    assert 'Traceback' not in result.stdout + result.stderr
    assert 'no matching converted files' in result.stdout   # the stray file, as before


# --- Stopping a conversion (#9) ---

def start_slow_run(ws, script, *args, segments=10):
    """Start `script` on `segments` segments that each take the fake FFmpeg 3 s,
    and return the process once the first is being written."""
    for i in range(segments):
        ws.add_media(f'{i:04d}.media', b'good')
    env = dict(ws.env, FAKE_FFMPEG_SLEEP='3', FAKE_FFMPEG_LOG=str(ws.root / 'calls'))
    proc = subprocess.Popen([sys.executable, script, '--accel', 'cpu', '--workers', '1', *args],
                            cwd=ws.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding='utf-8', errors='replace',
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
                            start_new_session=os.name != 'nt')
    deadline = time.monotonic() + 30
    while not ws.files('*.part') and time.monotonic() < deadline:
        time.sleep(0.1)
    return proc


def interrupt(proc, console=False):
    """Ctrl+C. On Windows: Ctrl+Break to the process group, as a console sends
    Ctrl+C to everything in it (this also ends the fake FFmpeg, whose .bat
    launcher terminate() wouldn't reach). Elsewhere: SIGINT to the process only,
    so the converter has to end FFmpeg itself, or with `console` to its whole
    process group, as a terminal does."""
    if os.name == 'nt':
        os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
    elif console:
        os.killpg(proc.pid, signal.SIGINT)
    else:
        os.kill(proc.pid, signal.SIGINT)
    started = time.monotonic()
    output, _ = proc.communicate(timeout=60)
    return output, time.monotonic() - started


def test_ctrl_c_stops_the_conversion_promptly(ws):
    # Ten segments of 3 s each, one at a time: 30 s if Ctrl+C didn't stop it
    proc = start_slow_run(ws, 'convert_media.py')

    output, took = interrupt(proc)

    assert took < 10, output
    assert proc.returncode == 1, output
    assert 'interrupted' in output and 'completed successfully' not in output
    assert len(list(ws.root.glob('calls.*'))) <= 2   # queued segments never started
    assert ws.files('*.part') == []
    assert len(ws.files('*.mp4')) < 10
    assert 'PROCESS INTERRUPTED' in (ws.root / 'output' / 'conversion_summary.txt').read_text(encoding='utf-8')
    # Nothing was listed as failed: the stopped segment is simply converted next time
    assert not (ws.root / 'output' / 'conversion_failed.txt').exists()


def test_ctrl_c_keeps_the_list_of_files_still_to_retry(ws):
    # An earlier run's list, which this run stops before it can check
    listed = ws.root / 'output' / 'conversion_failed.txt'
    listed.parent.mkdir(parents=True)
    other = str(ws.root / 'input' / 'DCIM' / '2025' / '10' / '01' / '1759300001_0001')
    listed.write_text(other + '\n', encoding='utf-8')
    proc = start_slow_run(ws, 'convert_media.py')

    output, _ = interrupt(proc)

    assert proc.returncode == 1, output
    assert listed.read_text(encoding='utf-8').splitlines() == [other]


def test_ctrl_c_during_a_retry_lists_what_it_didnt_get_to(ws, tmp_path):
    # Two events to retry: one converts, the other is stopped. An older,
    # relative entry for the stopped one appears once in the list afterwards
    done = ws.root / 'input' / 'DCIM' / '2025' / '10' / '01' / '1759300001_0001'
    done.mkdir(parents=True)
    (done / '0000.media').write_bytes(b'FAST')
    listed = ws.root / 'output' / 'conversion_failed.txt'
    listed.parent.mkdir(parents=True)
    slow = ws.root / GROUP_DIR
    listed.write_text(f'{done}\n{GROUP_DIR}\n', encoding='utf-8')
    proc = start_slow_run(ws, 'convert_media.py', '--retry-failed', '--workers', '2', segments=3)
    deadline = time.monotonic() + 30
    while not ws.files('1759300001_0001_0000.mp4') and time.monotonic() < deadline:
        time.sleep(0.1)

    output, _ = interrupt(proc)

    assert proc.returncode == 1, output
    assert ws.files('1759300001_0001_0000.mp4')   # the quick event converted before the stop
    assert not ws.files('1759300000_0001_0000.mp4')   # and the slow one was stopped
    assert listed.read_text(encoding='utf-8').splitlines() == [str(slow)]


def test_ctrl_c_stops_process_all_after_the_conversion_stops_cleanly(ws):
    proc = start_slow_run(ws, 'process_all.py')

    output, took = interrupt(proc, console=True)   # process_all relies on the step getting it too

    assert took < 10, output
    assert proc.returncode == 1, output
    assert 'Stopped by user' in output and 'Traceback' not in output
    assert '=== Step 2' not in output
    assert ws.files('*.part') == []
    assert 'PROCESS INTERRUPTED' in (ws.root / 'output' / 'conversion_summary.txt').read_text(encoding='utf-8')


# --- Unusual paths (#67) ---

@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='only Linux allows file names that are not UTF-8')
def test_paths_that_arent_utf8_are_kept_in_reports_lists_and_merges(ws):
    ws.add_media('0001.media', b'good')
    ws.add_media('0002.media', b'BAD')
    inp, out = os.fsdecode(b'in\xff'), os.fsdecode(b'out\xfe')
    (ws.root / 'input').rename(ws.root / inp)
    args = ['--accel', 'cpu', '--input-dir', f'{inp}/DCIM', '--output-dir', out]

    result = ws.run('convert_media.py', *args)
    assert result.returncode == 1, result.stdout + result.stderr   # 0002 failed, and is listed
    assert b'in\xff' in (ws.root / out / 'conversion_failed.txt').read_bytes()
    assert b'in\xff' in (ws.root / out / 'conversion_summary.txt').read_bytes()

    (ws.root / inp / GROUP_DIR.relative_to('input') / '0002.media').write_bytes(b'fixed')
    result = ws.run('convert_media.py', *args, '--retry-failed')
    assert result.returncode == 0, result.stdout + result.stderr

    result = ws.run('merge_videos.py', '--output-dir', out)
    assert result.returncode == 0, result.stdout + result.stderr
    [merged] = (ws.root / out / 'merged').rglob('merged_*.mp4')
    assert merged.read_bytes() == b'OKOKgoodOKfixed'

    # The whole pipeline, which also reads the merge's report (with a warning
    # naming a path that isn't UTF-8 in it)
    (ws.root / out / 'merged' / '2025' / '10' / '01' / 'merged_contents.json').unlink()
    result = ws.run('process_all.py', *args, '--splits-per-day', '2', '--no-validate')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Merge WARNING: kept 1 merged files' in result.stdout


@pytest.mark.skipif(os.name == 'nt', reason="Windows doesn't allow line breaks in file names")
def test_merge_explains_a_path_with_a_line_break(ws):
    ws.add_media('0001.media', b'good')
    out = 'out\nput'
    assert ws.run('convert_media.py', '--accel', 'cpu', '--output-dir', out).returncode == 0

    result = ws.run('merge_videos.py', '--output-dir', out)

    assert result.returncode == 1
    assert 'line break' in result.stdout and 'Traceback' not in result.stdout + result.stderr
    assert not list((ws.root / out / 'merged').rglob('merged_*.mp4'))


@pytest.mark.skipif(os.name == 'nt', reason="Windows doesn't allow line breaks in file names")
def test_a_failed_path_with_a_line_break_is_left_out_of_the_retry_list(ws):
    ws.add_media('0001.media', b'BAD')
    ws.add_media('0002.media', b'BAD')
    broken = ws.root / 'input' / 'DCIM' / '2025' / '10' / '01' / '1759300001_0001'
    broken.mkdir(parents=True)
    (broken / 'bad\nname.media').write_bytes(b'BAD')

    result = ws.run('convert_media.py', '--accel', 'cpu')

    assert result.returncode == 1
    assert "can't be listed for --retry-failed" in result.stdout
    listed = (ws.root / 'output' / 'conversion_failed.txt').read_text(encoding='utf-8').splitlines()
    # The other event isn't in the record format, so its segments are listed
    assert sorted(listed) == [str(ws.root / GROUP_DIR / '0001.media'), str(ws.root / GROUP_DIR / '0002.media')]


# --- Splitting a day by time (#7) ---

def test_splits_divide_the_day_into_equal_time_windows(ws):
    # Events at 06:26 and 14:46: with 5 windows of 4 h 48 min, they fall in the
    # second and fourth; the others have no events and get no file
    ws.add_media('0001.media', b'morning')
    ws.add_afternoon_media('0001.media', b'afternoon')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0

    result = ws.run('merge_videos.py', '--splits-per-day', '5')

    assert result.returncode == 0, result.stdout
    parts = {f.name: f.read_bytes() for f in ws.files('merged_*.mp4')}
    assert parts == {
        'merged_20251001_0448_20251001_0936_part2of5.mp4': b'OKOKmorning',
        'merged_20251001_1424_20251001_1912_part4of5.mp4': b'OKOKafternoon'}
    result = ws.run('validate_output.py')
    assert result.returncode == 0, result.stdout
    assert 'WARNING' not in result.stdout


@pytest.mark.parametrize('splits', ['0', '1441'])
def test_merge_rejects_splits_that_dont_make_whole_minute_windows(ws, splits):
    result = ws.run('merge_videos.py', '--splits-per-day', splits)

    assert result.returncode == 2
    assert '--splits-per-day must be 1 to 1440' in result.stderr


def test_merging_again_removes_a_part_whose_window_is_now_empty(ws):
    # An earlier version split two morning events by count: part 2 of 2, named
    # 12:00-24:00, held the second. Now both belong in part 1, and part 2 goes
    ws.add_media('0001.media', b'a')
    ws.add_media('0002.media', b'b')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    day = ws.root / 'output' / 'merged' / '2025' / '10' / '01'
    day.mkdir(parents=True)
    old = day / 'merged_20251001_1200_20251002_0000_part2of2.mp4'
    old.write_bytes(b'OKOKb')
    source = Path(os.path.relpath(ws.converted('0002.media'), ws.root / 'output')).as_posix()
    (day / 'merged_contents.json').write_text(json.dumps({old.name: [source]}), encoding='utf-8')

    result = ws.run('merge_videos.py', '--splits-per-day', '2')

    assert result.returncode == 0, result.stdout
    assert [f.name for f in day.glob('merged_*.mp4')] == ['merged_20251001_0000_20251001_1200_part1of2.mp4']
    assert ws.run('validate_output.py').returncode == 0


# --- Files the merge leaves out (#8) ---

def test_an_event_length_of_any_number_of_digits_is_converted_and_merged(ws):
    ws.add_media('0001.media', b'four')
    short = ws.root / 'input' / 'DCIM' / '2026' / '10' / '01' / '1759330000_60'
    short.mkdir(parents=True)
    (short / '0000.media').write_bytes(b'two')

    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    result = ws.run('merge_videos.py')

    assert result.returncode == 0, result.stdout
    [merged] = ws.files('merged_*.mp4')
    assert merged.read_bytes() == b'OKOKfourOKtwo'
    assert 'WARNING' not in result.stdout
    result = ws.run('validate_output.py')
    assert result.returncode == 0 and 'WARNING' not in result.stdout, result.stdout


def test_merge_warns_about_mp4_files_it_doesnt_know(ws):
    ws.add_media('0001.media', b'good')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    stray = ws.root / 'output' / 'holiday.mp4'
    stray.write_bytes(b'OKmine')
    assert ws.run('merge_videos.py').returncode == 0   # its own merged files don't count

    result = ws.run('merge_videos.py')

    assert result.returncode == 0, result.stdout
    assert "WARNING: 1 .mp4 files weren't merged" in result.stdout and 'holiday.mp4' in result.stdout
    [merged] = ws.files('merged_*.mp4')
    assert b'mine' not in merged.read_bytes()


# --- Skipping merges that are up to date (#39) ---

def merge_calls(ws):
    return [call for call in ws.ffmpeg_calls() if 'concat' in call]


def test_merge_skips_days_that_are_up_to_date(ws):
    ws.env['FAKE_FFMPEG_LOG'] = str(ws.root / 'calls')
    ws.add_media('0001.media', b'good')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0
    [merged] = ws.files('merged_*.mp4')
    made = merged.stat().st_mtime_ns

    result = ws.run('merge_videos.py')

    assert result.returncode == 0, result.stdout
    assert 'is up to date' in result.stdout
    assert len(merge_calls(ws)) == 1   # only the first run merged
    assert merged.stat().st_mtime_ns == made
    assert ws.run('validate_output.py').returncode == 0


def test_merge_redoes_a_day_with_a_new_event(ws):
    ws.env['FAKE_FFMPEG_LOG'] = str(ws.root / 'calls')
    ws.add_media('0001.media', b'good')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0
    ws.add_afternoon_media('0001.media', b'later')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0

    result = ws.run('merge_videos.py')

    assert 'up to date' not in result.stdout
    [merged] = ws.files('merged_*.mp4')
    assert merged.read_bytes() == b'OKOKgoodOKlater'


def test_merge_redoes_a_day_with_an_event_converted_again(ws):
    ws.add_media('0001.media', b'good')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0
    [merged] = ws.files('merged_*.mp4')
    converted = ws.converted('0001.media')
    converted.write_bytes(b'OKbetter')   # e.g. deleted and converted again by a newer version
    later = merged.stat().st_mtime + 10
    os.utime(converted, (later, later))

    result = ws.run('merge_videos.py')

    assert 'up to date' not in result.stdout
    assert merged.read_bytes() == b'OKOKbetter'


@pytest.mark.parametrize('how', ['force', 'no record'])
def test_merge_redoes_a_day_when_forced_or_unrecorded(ws, how):
    ws.env['FAKE_FFMPEG_LOG'] = str(ws.root / 'calls')
    ws.add_media('0001.media', b'good')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0
    if how == 'no record':   # merged by an earlier version
        (merged_dir(ws) / 'merged_contents.json').unlink()

    result = ws.run('merge_videos.py', *(['--force'] if how == 'force' else []))

    assert result.returncode == 0, result.stdout
    assert 'up to date' not in result.stdout
    assert len(merge_calls(ws)) == 2


def test_process_all_passes_force_merge_on(ws):
    result = ws.run('process_all.py', '--dry-run', '--force-merge')

    assert 'crittervid.merge' in result.stdout and '--force' in result.stdout


@pytest.mark.skipif(os.name != 'nt', reason='only Windows refuses to replace an open file')
def test_a_part_that_couldnt_be_replaced_is_merged_again_next_time(ws):
    ws.add_media('0001.media', b'good')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0
    [old] = ws.files('merged_*.mp4')
    # A new event whose file is older than the merged one (e.g. copied back
    # with its time kept), so only the record can tell the part is out of date
    ws.add_media('0002.media', b'more')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    earlier = old.stat().st_mtime - 10
    os.utime(ws.converted('0002.media'), (earlier, earlier))

    with open(old, 'rb'):   # e.g. still open in a video player
        assert ws.run('merge_videos.py').returncode == 1

    result = ws.run('merge_videos.py')

    assert result.returncode == 0, result.stdout
    assert 'up to date' not in result.stdout
    assert old.read_bytes() == b'OKOKgoodOKmore'
