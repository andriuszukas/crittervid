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
    assert 'UNPLAYABLE' in result.stdout


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


def test_process_all_output_is_in_order_when_piped(ws):
    # The GUI reads the pipeline through a pipe; step headers must not be held
    # back in a buffer and show up after the output of the steps they introduce
    ws.add_media('0001.media', b'good')

    lines = ws.run('process_all.py', '--accel', 'cpu').stdout.splitlines()

    def index(prefix):
        return next(i for i, line in enumerate(lines) if line.startswith(prefix))

    assert (index('=== Step 1') < index('Found 1 .media')
            < index('=== Step 2') < index('Merged 1 files')
            < index('=== Step 3') < index('Validating converted')
            < index('=== Summary'))
