"""Pipeline behaviour, using the fake ffmpeg from fake_ffmpeg.py."""
import os

import pytest


# --- Conversion: failed output must never look finished ---

def test_failed_conversion_leaves_no_output(ws):
    ws.add_media('0001.media', b'good')
    bad = ws.add_media('0002.media', b'BAD')

    result = ws.run('convert_media.py', '--accel', 'cpu')

    assert ws.converted('0001.media').read_bytes() == b'OKgood'
    assert not ws.converted('0002.media').exists()
    assert ws.files('*.part') == []
    failed_list = (ws.root / 'output' / 'conversion_failed.txt').read_text()
    assert str(bad.relative_to(ws.root)) in failed_list
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
    assert 'merged 2026/10/01 is 1.00 s long, but its 2 converted files add up to 2.00 s' in result.stdout


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
