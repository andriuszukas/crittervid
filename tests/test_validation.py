"""Validation and merge end points in detail, using the fake ffmpeg and ffprobe
(see fake_ffprobe.py for the markers that describe imperfect files)."""
import os
import shutil
import stat
import sys

import pytest

from conftest import TESTS_DIR, make_workspace

DAY = ('output', '2026', '10', '01')
MERGED_DAY = ('output', 'merged', '2026', '10', '01')
MERGED_ONE = 'merged_20261001_0000_20261002_0000_part1of1.mp4'


def event_file(ws, name, content, day=DAY):
    path = ws.root.joinpath(*day, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def merged_file(ws, content, name=MERGED_ONE):
    return event_file(ws, name, content, MERGED_DAY)


def lines(result, prefix):
    return [line for line in result.stdout.splitlines() if line.startswith(prefix)]


# --- Event files ---

def test_validate_reports_audio_that_does_not_match_the_video(ws):
    event_file(ws, '1790834400_0010.mp4', b'OKOKAUDIOSHORT')

    result = ws.run('validate_output.py')

    assert result.returncode == 1
    [line] = lines(result, 'ERROR')
    assert 'audio is 1.00 s long but the video' in line


def test_validate_only_warns_about_a_video_that_starts_late(ws):
    # Like a real event whose video starts after its audio: not damage
    event_file(ws, '1790834400_0010.mp4', b'OKLATESTART')

    result = ws.run('validate_output.py')

    assert result.returncode == 0, result.stdout
    [line] = lines(result, 'WARNING')
    assert 'starts at 0.80' in line
    assert lines(result, 'ERROR') == []


def test_validate_reports_an_event_file_without_video(ws):
    event_file(ws, '1790834400_0010.mp4', b'OKNOVIDEO')

    result = ws.run('validate_output.py')

    assert result.returncode == 1
    assert 'no video' in result.stdout


def test_validate_does_not_need_audio_in_segment_files(ws):
    event_file(ws, '1790834400_0010/1790834400_0010_0.mp4', b'OKNOAUDIO')

    result = ws.run('validate_output.py')

    assert result.returncode == 0, result.stdout
    assert lines(result, 'ERROR') == []


def test_validate_reports_a_stream_without_a_length(ws):
    event_file(ws, '1790834400_0010.mp4', b'OKNODURATION')

    result = ws.run('validate_output.py')

    assert result.returncode == 1
    assert 'length unknown' in result.stdout


def test_validate_reports_a_file_that_is_not_mp4(ws):
    path = event_file(ws, '1790834400_0010.mp4', b'OKNOTMP4')

    result = ws.run('validate_output.py')

    assert result.returncode == 1
    assert any(path.name in line and 'not an MP4 file' in line for line in lines(result, 'ERROR'))


# --- Unreadable files ---

def test_validate_retries_an_unreadable_file_once(ws):
    event_file(ws, '1790834400_0010.mp4', b'OKa')
    flag = ws.root / 'fail_once'
    flag.write_text('')
    ws.env['FAKE_FFPROBE_FAIL_ONCE'] = str(flag)

    result = ws.run('validate_output.py')

    assert not flag.exists()   # the first call failed
    assert result.returncode == 0, result.stdout


def test_validate_says_why_a_file_is_unreadable(ws):
    path = event_file(ws, '1790834400_0010.mp4', b'corrupt')

    result = ws.run('validate_output.py')

    assert result.returncode == 1
    assert any(path.name in line and 'unreadable (' in line and 'Invalid data found when processing input' in line
               for line in lines(result, 'ERROR')), result.stdout


def test_validate_reports_an_unreadable_merged_file(ws):
    event_file(ws, '1790834400_0010.mp4', b'OKa')
    path = merged_file(ws, b'corrupt')

    result = ws.run('validate_output.py')

    assert result.returncode == 1
    assert any(path.name in line and 'unreadable' in line for line in lines(result, 'ERROR'))


def test_unreadable_converted_file_does_not_also_make_its_day_unmatched(ws):
    event_file(ws, '1790834400_0010.mp4', b'OKa')
    event_file(ws, '1790835000_0010.mp4', b'corrupt')
    merged_file(ws, b'OKOKa')

    result = ws.run('validate_output.py')

    assert result.returncode == 1
    assert 'unreadable' in result.stdout
    assert 'no matching converted files' not in result.stdout


# --- Merged days ---

def test_validate_reports_a_merged_file_without_audio(ws):
    event_file(ws, '1790834400_0010.mp4', b'OKa')
    event_file(ws, '1790835000_0010.mp4', b'OKb')
    path = merged_file(ws, b'OKOKaOKbNOAUDIO')

    result = ws.run('validate_output.py')

    assert result.returncode == 1
    assert any(path.name in line and 'no audio, although its converted files have audio' in line
               for line in lines(result, 'ERROR')), result.stdout


def test_validate_reports_a_day_with_no_merged_file(ws):
    event_file(ws, '1790834400_0010.mp4', b'OKa')
    merged_file(ws, b'OKOKa')
    event_file(ws, '1790920800_0010.mp4', b'OKb', ('output', '2026', '10', '02'))

    result = ws.run('validate_output.py')

    assert result.returncode == 1
    assert 'no merged file for 2026/10/02' in result.stdout


def test_validate_counts_the_lead_in_of_a_late_video_inside_a_merged_part(ws):
    # The merge joins each file up to where its video ends, so a later file whose
    # video starts late (#77) brings its lead-in into the merged part
    event_file(ws, '1790834400_0010.mp4', b'OKa')
    event_file(ws, '1790835000_0010.mp4', b'OKLATESTART')
    merged_file(ws, b'OKOKaOKLATESTART')   # 1 s + (0.8 + 1) s

    result = ws.run('validate_output.py')

    assert result.returncode == 0, result.stdout
    assert not lines(result, 'WARNING: merged')


def test_validate_leaves_out_the_lead_in_of_a_late_first_video(ws):
    # A merged file starts at its first frame, so the first file's lead-in isn't in it
    event_file(ws, '1790834400_0010.mp4', b'OKLATESTART')
    event_file(ws, '1790835000_0010.mp4', b'OKb')
    merged_file(ws, b'OKOKxOKb')   # 1 s + 1 s

    assert ws.run('validate_output.py').returncode == 0


def test_validate_reports_a_merged_part_missing_footage(ws):
    event_file(ws, '1790834400_0010.mp4', b'OKa')
    event_file(ws, '1790835000_0010.mp4', b'OKLATESTART')
    merged_file(ws, b'OKOKa')   # the second event is missing

    result = ws.run('validate_output.py')

    assert result.returncode == 1
    assert 'merged 2026/10/01 part 1 of 1 is 1.00 s long, but its 2 converted files add up to 2.80 s' in result.stdout


def test_validate_checks_each_set_of_merged_files_and_warns_about_leftovers(ws):
    # A day merged with one split setting, then again with another
    event_file(ws, '1790834400_0010.mp4', b'OKa')
    event_file(ws, '1790835000_0010.mp4', b'OKb')
    merged_file(ws, b'OKOKaOKb')
    merged_file(ws, b'OKOKa', 'merged_20261001_0000_20261001_1200_part1of2.mp4')
    merged_file(ws, b'OKOKb', 'merged_20261001_1200_20261002_0000_part2of2.mp4')

    result = ws.run('validate_output.py')

    assert result.returncode == 0, result.stdout
    assert any('split' in line for line in lines(result, 'WARNING')), result.stdout


def test_validate_fails_when_a_set_of_merged_files_is_short(ws):
    event_file(ws, '1790834400_0010.mp4', b'OKa')
    event_file(ws, '1790835000_0010.mp4', b'OKb')
    merged_file(ws, b'OKOKaOKb')
    merged_file(ws, b'OKOKa', 'merged_20261001_0000_20261001_1200_part1of2.mp4')

    result = ws.run('validate_output.py')

    assert result.returncode == 1
    assert 'ERROR: merged 2026/10/01: part 2 of 2 is missing' in result.stdout


def test_validate_ignores_old_segment_files_next_to_the_event_file(ws):
    event_dir = ws.converted('0001.media').parent
    event_dir.mkdir(parents=True)
    ws.converted('0001.media').write_bytes(b'OKold')
    (event_dir.parent / f'{event_dir.name}.mp4').write_bytes(b'OKnew')
    assert ws.run('merge_videos.py').returncode == 0

    result = ws.run('validate_output.py')

    assert result.returncode == 0, result.stdout


# --- Merge ---

def test_merge_ends_each_file_where_its_video_ends(ws):
    # A video starting at 0.8 s and 1 s long ends at 1.8 s
    event_file(ws, '1790834400_0010.mp4', b'OKLATESTART')
    event_file(ws, '1790835000_0010.mp4', b'OKb')
    ws.env['FAKE_FFMPEG_LOG'] = str(ws.root / 'ffmpeg_calls.jsonl')

    assert ws.run('merge_videos.py').returncode == 0

    [text] = ws.concat_lists()
    points = {}
    for line in text.splitlines():
        name, _, value = line.partition(' ')
        if name in ('outpoint', 'duration'):
            points.setdefault(name, []).append(float(value))
    assert points == {'outpoint': [pytest.approx(1.8), pytest.approx(1.0)],
                      'duration': [pytest.approx(1.8), pytest.approx(1.0)]}


def test_merging_again_with_other_splits_removes_the_earlier_files(ws):
    ws.add_media('0001.media', b'a')
    ws.add_media('0002.media', b'b')
    assert ws.run('convert_media.py', '--accel', 'cpu').returncode == 0
    assert ws.run('merge_videos.py').returncode == 0
    assert [f.name for f in ws.files('merged_*.mp4')] == ['merged_20251001_0000_20251002_0000_part1of1.mp4']

    assert ws.run('merge_videos.py', '--splits-per-day', '2').returncode == 0

    assert [f.name for f in ws.files('merged_*.mp4')] == [
        'merged_20251001_0000_20251001_1200_part1of2.mp4',
        'merged_20251001_1200_20251002_0000_part2of2.mp4']
    result = ws.run('validate_output.py')
    assert result.returncode == 0, result.stdout
    assert 'WARNING' not in result.stdout


# --- Without ffprobe ---

def test_validate_without_ffprobe_only_decodes(tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    fake = TESTS_DIR / 'fake_ffmpeg.py'
    if os.name == 'nt':
        (bin_dir / 'ffmpeg.bat').write_text(f'@"{sys.executable}" "{fake}" %*\r\n')
        system = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
    else:
        launcher = bin_dir / 'ffmpeg'
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
        system = '/usr/bin:/bin'
    ws = make_workspace(tmp_path)
    ws.env['PATH'] = str(bin_dir) + os.pathsep + system
    if shutil.which('ffprobe', path=ws.env['PATH']):
        pytest.skip('a real ffprobe is installed in the system folders')
    event_file(ws, '1790834400_0010.mp4', b'OKa')
    ws.env['FAKE_FFMPEG_LOG'] = str(ws.root / 'ffmpeg_calls.jsonl')

    result = ws.run('validate_output.py')

    assert result.returncode == 0, result.stdout + result.stderr
    assert 'ffprobe' in result.stdout and 'only' in result.stdout
    assert len([args for args in ws.ffmpeg_calls() if args[-1] == '-']) == 1


# --- Deep decoding ---

def test_deep_validation_retries_a_failed_hardware_decode_on_the_cpu(ws):
    event_file(ws, '1790834400_0010.mp4', b'OKa')
    ws.env['FAKE_FFMPEG_LOG'] = str(ws.root / 'ffmpeg_calls.jsonl')
    ws.env['FAKE_FFMPEG_FAIL_HWACCEL'] = '1'

    result = ws.run('validate_output.py', '--deep', '--accel', 'cuda')

    assert result.returncode == 0, result.stdout
    decodes = [args for args in ws.ffmpeg_calls() if args[-1] == '-']
    assert ['-hwaccel' in args for args in decodes].count(True) >= 1
    assert ['-hwaccel' in args for args in decodes].count(False) == 1


# --- The output folder ---

def test_validate_reports_a_missing_output_folder(ws):
    result = ws.run('validate_output.py', '--output-dir', 'nowhere')

    assert result.returncode == 1
    assert 'ERROR: output folder not found: nowhere' in result.stdout


def test_validate_accepts_an_output_folder_without_videos(ws):
    (ws.root / 'empty').mkdir()

    result = ws.run('validate_output.py', '--output-dir', 'empty')

    assert result.returncode == 0
    assert 'No output videos' in result.stdout


# --- Second review: real #77 shape, finer tolerances, merged audio ---

def test_validate_accepts_audio_running_to_the_end_of_a_late_video(ws):
    # Real #77 files: video from 0.8 s, audio from 0 to the video's end
    event_file(ws, '1790834400_0010.mp4', b'OKLATEAUDIOEND')

    result = ws.run('validate_output.py')

    assert result.returncode == 0, result.stdout
    assert lines(result, 'WARNING') and 'starts at 0.80 s' in result.stdout


def test_validate_catches_a_small_difference_in_a_merged_day(ws):
    # 50 ms is far more than a correct merge differs by (measured: 0.000 s)
    event_file(ws, '1790834400_0010.mp4', b'OKa')
    event_file(ws, '1790835000_0010.mp4', b'OKb')
    merged_file(ws, b'OKOKaOKbPLUS50MS')

    result = ws.run('validate_output.py')

    assert result.returncode == 1
    assert 'is 2.05 s long, but its 2 converted files add up to 2.00 s' in result.stdout


def test_validate_reports_merged_audio_longer_than_its_video(ws):
    event_file(ws, '1790834400_0010.mp4', b'OKa')
    event_file(ws, '1790835000_0010.mp4', b'OKb')
    merged_file(ws, b'OKOKaOKbAUDIOSHORT')

    result = ws.run('validate_output.py')

    assert result.returncode == 1
    assert any('audio is 1.00 s long but the video is 2.00 s long' in line for line in lines(result, 'ERROR'))
