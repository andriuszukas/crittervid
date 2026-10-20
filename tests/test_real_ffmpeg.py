"""End-to-end runs with the real ffmpeg. Skipped when ffmpeg isn't on PATH."""
import json
import subprocess

import pytest

from media_fixture import utc, write_event


def make_clip(ws, name, seconds=1, size='160x120', audio_rate=44100):
    path = ws.add_media(name, b'')
    subprocess.run(
        ['ffmpeg', '-v', 'error', '-y',
         '-f', 'lavfi', '-i', f'testsrc=duration={seconds}:size={size}:rate=10',
         '-f', 'lavfi', '-i', f'sine=duration={seconds}:sample_rate={audio_rate}',
         '-c:v', 'libx264', '-c:a', 'aac', '-ac', '1', '-f', 'mpegts', str(path)],
        check=True,
    )


def test_full_pipeline(real_ws):
    make_clip(real_ws, '0001.media')
    make_clip(real_ws, '0002.media')

    result = real_ws.run('process_all.py', '--accel', 'cpu')

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(real_ws.files('1759300000_0001_*.mp4')) == 2
    assert len(real_ws.files('merged_*.mp4')) == 1
    assert real_ws.files('*.part') == []


def test_full_pipeline_with_quote_and_non_ascii_output_dir(real_ws):
    out_dir = "out O'Brien ėų 野"
    make_clip(real_ws, '0001.media')
    make_clip(real_ws, '0002.media')

    result = real_ws.run('process_all.py', '--accel', 'cpu', '--output-dir', out_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(list((real_ws.root / out_dir).rglob('merged_*.mp4'))) == 1


def test_unreadable_input_fails_cleanly(real_ws):
    make_clip(real_ws, '0001.media')
    real_ws.add_media('0002.media', bytes(range(256)) * 20)

    result = real_ws.run('convert_media.py', '--accel', 'cpu')

    assert real_ws.converted('0001.media').exists()
    assert not real_ws.converted('0002.media').exists()
    assert real_ws.files('*.part') == []
    assert result.returncode == 1


def video_seconds(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v', '-show_entries', 'stream=duration',
                          '-of', 'json', str(path)], capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)['streams'][0]['duration'])


@pytest.mark.parametrize('event_hour', [6, 12], ids=['event first', 'fallback first'])
def test_fallback_and_event_files_of_one_day_merge_to_their_sum(real_ws, event_hour):
    # The fallback clip is from 07:46 UTC (see conftest.GROUP_DIR); both are the same day
    # Same video size and audio format as the event, so the merge can join them
    make_clip(real_ws, '0001.media', seconds=5, size='320x240', audio_rate=8000)
    write_event(real_ws.root / 'input' / 'DCIM', utc(2025, 10, 1, event_hour, 0), 10)
    assert real_ws.run('convert_media.py', '--accel', 'cpu').returncode == 0

    result = real_ws.run('merge_videos.py')

    assert result.returncode == 0, result.stdout + result.stderr
    [merged] = real_ws.files('merged_*.mp4')
    assert video_seconds(merged) == pytest.approx(15, abs=0.5)
